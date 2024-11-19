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
from datetime import datetime, timezone


LOGLEVEL = os.environ.get('LOGLEVEL', 'INFO').upper()
easee_username = os.environ.get('EASEE_USERNAME', None)
easee_password = os.environ.get('EASEE_PASSWORD', None)
easee_chargers = os.environ.get("EASEE_CHARGERS").split(",")
polling_interval = int(os.environ.get('POLLING_INTERVAL', 300))
mqtt_host = os.environ.get('MQTT_HOST', None)
mqtt_port = int(os.environ.get('MQTT_PORT', 1833))
mqtt_password = os.environ.get('MQTT_PASSWORD', None)
mqtt_username = os.environ.get('MQTT_USERNAME', None)
access_token = None
token_expiration = time.time()

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

def get_state(charger_id):
    global access_token
    check_access_token()
    url = f"https://api.easee.cloud/api/chargers/{charger_id}/state"
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + access_token}
    resp = requests.request("GET", url = url, headers=headers)
    parsed = resp.json()
    logging.debug("State")
    logging.debug(parsed)
    if resp.status_code != 200:
        logging.warning(f"Response code {resp.status_code} when trying to get_state")
    return parsed


def publish_state(client, charger):
    state = get_state(charger)
    config = get_config(charger)
    latest_session = get_latest_session(charger)
    latest_pulse = datetime.strptime(state['latestPulse'], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone(tz=None).strftime("%Y-%m-%d %H:%M:%S")
    logging.debug(f"Publish_state - Latest pulse: {latest_pulse}")

    client.publish(f"easee2MQTT/{charger}/energy_consumption", round(state['lifetimeEnergy'],2))
    client.publish(f"easee2MQTT/{charger}/current_session", round(state['sessionEnergy'],2))
    client.publish(f"easee2MQTT/{charger}/previous_session", round(latest_session['sessionEnergy'],2))
    client.publish(f"easee2MQTT/{charger}/voltage", round(state['voltage'],1))
    client.publish(f"easee2MQTT/{charger}/power", round(state['totalPower'],2))
    client.publish(f"easee2MQTT/{charger}/cable_lock", state['cableLocked'])
    client.publish(f"easee2MQTT/{charger}/charging_enabled", config['isEnabled'])
    client.publish(f"easee2MQTT/{charger}/smartcharging_enabled", state['smartCharging'])
    client.publish(f"easee2MQTT/{charger}/latest_pulse", latest_pulse)
    client.publish(f"easee2MQTT/{charger}/charging_current", state['dynamicChargerCurrent'])
    client.publish(f"easee2MQTT/{charger}/chargerOpMode", convertToAF(state['chargerOpMode']))


def on_message(client, userdata, message):
    logging.info(f"Message received on topic: {message.topic}, payload: {str(message.payload.decode('utf-8'))}")
    global access_token
    charger = message.topic.split("/")[1]
    headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + access_token}

    if message.topic.split("/")[2] == "cable_lock":
        url = "https://api.easee.cloud/api/chargers/"+charger+"/commands/lock_state"
        data = {
            "state": str(message.payload.decode("utf-8"))
        }
        resp = requests.post(url, headers= headers, json = data)
        callback_topic = f"easee2MQTT/{charger}/cable_lock"

    elif message.topic.split("/")[2] == "charging_enabled":
        url = "https://api.easee.cloud/api/chargers/"+charger+"/settings"
        if (str(message.payload.decode("utf-8")).casefold() == "true" or
            str(message.payload.decode("utf-8")).casefold() == "false"):
            data = {
                'enabled' : str(message.payload.decode("utf-8")).title()
            }
            resp = requests.post(url, headers=headers, json = data)
            callback_topic = f"easee2MQTT/{charger}/charging_enabled"

        else:
            logging.warning("Couldn't identify payload. 'true' or 'false' is only supported values.")

    elif message.topic.split("/")[2] == "smartcharging_enabled":
        if (str(message.payload.decode("utf-8")).casefold() == "true" or
            str(message.payload.decode("utf-8")).casefold() == "false"):
            url = "https://api.easee.cloud/api/chargers/"+charger+"/settings"
            data = {
                "smartCharging" : message.payload.decode("utf-8").title()
            }
            resp = requests.post(url, headers=headers, json = data)
            callback_topic = f"easee2MQTT/{charger}/smartcharging_enabled"

    elif message.topic.split("/")[2] == "charging_current":
        if float(message.payload.decode('utf-8')) < 33.0:
            url = "https://api.easee.cloud/api/chargers/"+charger+"/settings"
            data = {
                "dynamicChargerCurrent" : message.payload.decode('utf-8')
            }
            resp = requests.post(url, headers=headers, json = data)
            callback_topic = f"easee2MQTT/{charger}/charging_current"
        else:
            logging.warning(f"Couldn't publish new charging_current")
    
    try:
        if message.topic.split("/")[2] != "ping":
            logging.info(f"Manually publishing setting {message.topic.split('/')[2]} for {charger}")
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

    for charger in easee_chargers:
        logging.info(f"Subscribing to topics for charger {charger}.")
        client.subscribe("easee2MQTT/"+charger+"/cable_lock/set")
        client.subscribe("easee2MQTT/"+charger+"/charging_enabled/set")
        client.subscribe("easee2MQTT/"+charger+"/ping")
        client.subscribe("easee2MQTT/"+charger+"/smartcharging_enabled/set")
        client.subscribe("easee2MQTT/"+charger+"/charging_current/set")
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