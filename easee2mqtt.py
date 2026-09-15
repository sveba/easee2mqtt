#!/usr/bin/python3
import requests
import json
import time
import sys
import datetime
import logging
import paho.mqtt.client as mqtt
from requests.api import request
import os
from datetime import datetime


LOGLEVEL = os.environ.get('LOGLEVEL', 'INFO').upper()
easee_username = os.environ.get('EASEE_USERNAME', None)
easee_password = os.environ.get('EASEE_PASSWORD', None)
easee_chargers = os.environ.get("EASEE_CHARGERS").split(",")
polling_interval = int(os.environ.get('POLLING_INTERVAL', 300))
mqtt_host = os.environ.get('MQTT_HOST', None)
mqtt_port = int(os.environ.get('MQTT_PORT', 1883))
mqtt_password = os.environ.get('MQTT_PASSWORD', None)
mqtt_username = os.environ.get('MQTT_USERNAME', None)
mqtt_root_topic = os.environ.get('MQTT_ROOT_TOPIC', 'easee2MQTT').strip("/")
access_token = None
token_expiration = time.time()
charger_serial_numbers = {}

STATE_OBSERVATIONS = {
    48: "dynamicChargerCurrent",
    102: "smartCharging",
    103: "cableLocked",
    109: "chargerOpMode",
    120: "totalPower",
    121: "sessionEnergy",
    124: "lifetimeEnergy",
}
VOLTAGE_OBSERVATIONS = (202, 203, 204, 205, 206, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199)

logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)],
                    level=LOGLEVEL,
                    format="[%(asctime)s] %(levelname)s %(message)s",
                    datefmt='%Y-%m-%d %H:%M:%S')

def check_access_token():
    global access_token, token_expiration, easee_username, easee_password
    if token_expiration - time.time() < 350:
        logging.info("Token expires in less than 350 seconds. Fetching a new token.")
    
        url = "https://api.easee.cloud/api/accounts/login"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json-patch+json"
        }

        body = {
            "userName": easee_username,
            "password": easee_password
        }

        response = requests.request("POST", url, headers=headers, json=body)
        logging.debug(f"Response from get_access_log: {response}")
        if response.status_code == 200:
            logging.info("Successfully connected to Easee")
        else:
            logging.warning("Failed to connect to Easee. Response code: "
                            f"{response.status_code}")
            return False
        json_obj = json.loads(response.text)
        token_expiration = time.time() + json_obj['expiresIn']
        access_token = json_obj['accessToken']

        logging.info("Successfully retrieved and stored a new token.")
    else:
        logging.debug("Token is not up for refresh.")


def response_codes(code):
    if code == 200 or code == 202:
        return "Command successfully sent to charger"
    elif code == 400:
        return "Command has missing/invalid values"
    elif code == 401:
        return "Missing authorization data. Check 'Authorization' header"
    elif code == 403:
        return "Forbidden. Authorization set, but access to resource is denied"
    elif code == 415:
        return "Payload format is in an unsupported format"
    elif code == 500:
        return "Oops! Unexpected internal error. Request has been logged and code monkeys warned"
    elif code == 503:
        return "Server gateway cannot reach API. Try again in about a minute..."
    elif code == 504:
        return "Unable to deliver commands upstream. End device is not reachable, or a problem with queueing the device command"
    else:
        return f"Unknown response code: {code}"

def convertToAF(code):
    if code == 0 or code == 1:
        return "A"
    elif code == 2:
        return "B"
    elif code == 3:
        return "C"
    else:
        return f"Unknown state code: {code}"

def mqtt_topic(suffix):
    return f"{mqtt_root_topic}/{suffix}"

def parse_mqtt_topic(topic):
    prefix = f"{mqtt_root_topic}/"
    if not topic.startswith(prefix):
        return None
    return topic[len(prefix):].split("/")[0]

def get_latest_session(charger_id):
    global access_token
    check_access_token()
    details_url = f"https://api.easee.cloud/api/chargers/{charger_id}/sessions/latest"

    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + access_token}

    resp = requests.request("GET", url = details_url, headers = headers)
    parsed = resp.json()
    if resp.status_code != 200:
        logging.warning(f"Response code {resp.status_code} when trying to get_latest_session")
    return parsed

def find_serial_number(details):
    if isinstance(details, dict):
        for key in ("serialNumber", "serialNo", "serial", "chargerSerialNumber"):
            if details.get(key):
                return str(details[key])
        for value in details.values():
            serial_number = find_serial_number(value)
            if serial_number:
                return serial_number
    elif isinstance(details, list):
        for value in details:
            serial_number = find_serial_number(value)
            if serial_number:
                return serial_number

def get_charger_serial_number(charger_id):
    global access_token, charger_serial_numbers
    if charger_id not in charger_serial_numbers:
        check_access_token()
        url = f"https://api.easee.com/api/chargers/{charger_id}/details"
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + access_token}
        resp = requests.request("GET", url = url, headers=headers)
        details = resp.json()
        if resp.status_code != 200:
            logging.warning(f"Response code {resp.status_code} when trying to get charger details")
        charger_serial_numbers[charger_id] = find_serial_number(details)
        if not charger_serial_numbers[charger_id]:
            raise KeyError(f"Could not find serial number in charger details for {charger_id}")
    return charger_serial_numbers[charger_id]

def _observation_id(observation):
    return observation.get("id") or observation.get("observationId") or observation.get("ID")

def _observation_value(observation):
    return observation.get("value", observation.get("Value"))

def _observation_timestamp(observation):
    return observation.get("timestamp") or observation.get("Timestamp")

def _typed_observation_value(observation_id, value):
    if observation_id in (102, 103):
        return str(value).casefold() == "true" if isinstance(value, str) else value
    if observation_id == 109:
        return int(value)
    if observation_id in STATE_OBSERVATIONS or observation_id in VOLTAGE_OBSERVATIONS:
        return float(value)
    return value

def observations_to_state(observations):
    if isinstance(observations, dict):
        observations = observations.get("data") or observations.get("observations") or []

    state = {}
    latest_pulse = None
    voltages = {}

    for observation in observations:
        observation_id = _observation_id(observation)
        value = _observation_value(observation)
        timestamp = _observation_timestamp(observation)
        value = _typed_observation_value(observation_id, value)

        if observation_id in STATE_OBSERVATIONS:
            state[STATE_OBSERVATIONS[observation_id]] = value
        elif observation_id in VOLTAGE_OBSERVATIONS:
            voltages[observation_id] = value

        if timestamp and (latest_pulse is None or timestamp > latest_pulse):
            latest_pulse = timestamp

    for observation_id in VOLTAGE_OBSERVATIONS:
        if observation_id in voltages:
            state["voltage"] = voltages[observation_id]
            break

    state["latestPulse"] = latest_pulse
    return state

def format_latest_pulse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz=None).strftime("%Y-%m-%d %H:%M:%S")

def get_state(charger_id):
    global access_token
    check_access_token()
    ids = ",".join(str(id) for id in (*STATE_OBSERVATIONS, *VOLTAGE_OBSERVATIONS))
    serial_number = get_charger_serial_number(charger_id)
    url = f"https://api.easee.com/state/{serial_number}/observations"
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + access_token}
    resp = requests.request("GET", url = url, headers=headers, params={"ids": ids})
    parsed = observations_to_state(resp.json())
    logging.debug("State")
    logging.debug(parsed)
    if resp.status_code != 200:
        logging.warning(f"Response code {resp.status_code} when trying to get_state")
    return parsed


def publish_state(client, charger):
    state = get_state(charger)
    config = get_config(charger)
    latest_session = get_latest_session(charger)
    latest_pulse = format_latest_pulse(state['latestPulse'])
    logging.debug(f"Publish_state - Latest pulse: {latest_pulse}")

    client.publish(mqtt_topic("energy_consumption"), round(state['lifetimeEnergy'],2))
    client.publish(mqtt_topic("current_session"), round(state['sessionEnergy'],2))
    client.publish(mqtt_topic("previous_session"), round(latest_session['sessionEnergy'],2))
    client.publish(mqtt_topic("voltage"), round(state['voltage'],1))
    client.publish(mqtt_topic("power"), round(state['totalPower'],2))
    client.publish(mqtt_topic("cable_lock"), state['cableLocked'])
    client.publish(mqtt_topic("charging_enabled"), config['isEnabled'])
    client.publish(mqtt_topic("smartcharging_enabled"), state['smartCharging'])
    client.publish(mqtt_topic("latest_pulse"), latest_pulse)
    client.publish(mqtt_topic("charging_current"), state['dynamicChargerCurrent'])
    client.publish(mqtt_topic("chargerOpMode"), convertToAF(state['chargerOpMode']))


def on_message(client, userdata, message):
    logging.info(f"Message received on topic: {message.topic}, payload: {str(message.payload.decode('utf-8'))}")
    global access_token
    charger = easee_chargers[0]
    setting = parse_mqtt_topic(message.topic)
    if not setting:
        return
    headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + access_token}

    if setting == "cable_lock":
        url = "https://api.easee.cloud/api/chargers/"+charger+"/commands/lock_state"
        data = {
            "state": str(message.payload.decode("utf-8"))
        }
        resp = requests.post(url, headers= headers, json = data)
        callback_topic = mqtt_topic("cable_lock")

    elif setting == "charging_enabled":
        url = "https://api.easee.cloud/api/chargers/"+charger+"/settings"
        if (str(message.payload.decode("utf-8")).casefold() == "true" or
            str(message.payload.decode("utf-8")).casefold() == "false"):
            data = {
                'enabled' : str(message.payload.decode("utf-8")).title()
            }
            resp = requests.post(url, headers=headers, json = data)
            callback_topic = mqtt_topic("charging_enabled")

        else:
            logging.warning("Couldn't identify payload. 'true' or 'false' is only supported values.")

    elif setting == "smartcharging_enabled":
        if (str(message.payload.decode("utf-8")).casefold() == "true" or
            str(message.payload.decode("utf-8")).casefold() == "false"):
            url = "https://api.easee.cloud/api/chargers/"+charger+"/settings"
            data = {
                "smartCharging" : message.payload.decode("utf-8").title()
            }
            resp = requests.post(url, headers=headers, json = data)
            callback_topic = mqtt_topic("smartcharging_enabled")

    elif setting == "charging_current":
        if float(message.payload.decode('utf-8')) < 33.0:
            url = "https://api.easee.cloud/api/chargers/"+charger+"/settings"
            data = {
                "dynamicChargerCurrent" : message.payload.decode('utf-8')
            }
            resp = requests.post(url, headers=headers, json = data)
            callback_topic = mqtt_topic("charging_current")
        else:
            logging.warning(f"Couldn't publish new charging_current")
    
    try:
        if setting != "ping":
            logging.info(f"Manually publishing setting {setting} for {charger}")
            client.publish(callback_topic, message.payload.decode('utf-8')) 
    except:
        logging.warning(f"Couldn't publish manually for message: {message}")

    try:
        #Log a warning if we still have a status_code
        if resp.status_code == 200 or resp.status_code ==202:
            logging.info(f"Response {response_codes(resp.status_code)} - Payload: {message.payload.decode('utf-8')}")
        else:
            logging.warning(f"Failed to send command to charger. Response code {resp.status_code} - {response_codes(resp.status_code)}")
    except:
        logging.warning(f"No status_code from recieved message: {message}")


def get_config(charger):
    url = "https://api.easee.cloud/api/chargers/"+charger+"/config"
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + access_token}
    resp = requests.request("GET", url = url, headers=headers)
    parsed = resp.json()
    if resp.status_code != 200:
        logging.warning(f"Response code {resp.status_code} when trying to get_latest_session")
    return parsed

def main():
    logging.info("Script is starting. Looking for settings")
    global easee_chargers, mqtt_host, mqtt_port, mqtt_username, mqtt_password, polling_interval
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,"Easee2MQTT")
    logging.debug(f"MQTT: Connect to {mqtt_host}:{mqtt_port}")
    if mqtt_password:
        client.username_pw_set(username=mqtt_username, password=mqtt_password)
    client.connect(mqtt_host, mqtt_port)
    client.loop_start()

    logging.info(f"Subscribing to topics below {mqtt_root_topic}.")
    client.subscribe(mqtt_topic("cable_lock/set"))
    client.subscribe(mqtt_topic("charging_enabled/set"))
    client.subscribe(mqtt_topic("ping"))
    client.subscribe(mqtt_topic("smartcharging_enabled/set"))
    client.subscribe(mqtt_topic("charging_current/set"))
    client.on_message = on_message

    try:
        while True:
            for charger in easee_chargers:
                try:
                    logging.debug(f"Fetching and publishing latest stats of {charger}")
                    publish_state(client, charger)
                except Exception as err:
                    logging.error(err)
                    logging.error(f"Failed to fetch and publish new stats of {charger}. Will retry in {polling_interval} seconds")

            time.sleep(polling_interval)
    except Exception as err:
        logging.error(err)
    client.loop_stop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting program")
