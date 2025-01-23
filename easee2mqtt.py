#!/usr/bin/python3
import json
import time
import os
import sys
import logging
import requests
import paho.mqtt.client as mqtt


LOGLEVEL = os.environ.get('LOGLEVEL', 'INFO').upper()
easee_username = os.environ.get('EASEE_USERNAME', None)
easee_password = os.environ.get('EASEE_PASSWORD', None)
easee_chargers = os.environ.get("EASEE_CHARGERS").split(",")
polling_interval = int(os.environ.get('POLLING_INTERVAL', 300))
mqtt_host = os.environ.get('MQTT_HOST', None)
mqtt_port = int(os.environ.get('MQTT_PORT', 1833))
mqtt_password = os.environ.get('MQTT_PASSWORD', None)
mqtt_username = os.environ.get('MQTT_USERNAME', None)
ACCESS_TOKEN = None
token_expiration = time.time()
EASEE_API_BASE = "https://api.easee.cloud/api"

logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)],
                    level=LOGLEVEL,
                    format="[%(asctime)s] %(levelname)s %(message)s",
                    datefmt='%Y-%m-%d %H:%M:%S')


def check_access_token():
    global ACCESS_TOKEN, token_expiration
    if token_expiration - time.time() < 350:
        logging.info(
            "Token expires in less than 350 seconds. Fetching a new token.")
        url = f"{EASEE_API_BASE}/accounts/login"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json-patch+json"
        }

        body = {
            "userName": easee_username,
            "password": easee_password
        }

        response = requests.request("POST", url, headers=headers, json=body)
        logging.debug("Response from get_access_log: %s", response)
        if response.status_code == 200:
            logging.info("Successfully connected to Easee")
        else:
            logging.warning(
                "Failed to connect to Easee. Response code: %s", response.status_code)
            sys.exit(1)
        json_obj = json.loads(response.text)
        token_expiration = time.time() + json_obj['expiresIn']
        ACCESS_TOKEN = json_obj['accessToken']
        logging.info("Successfully retrieved and stored a new token.")
    else:
        logging.debug("Token is not up for refresh.")


def response_codes(code):
    msg = f"Unknown response code: {code}"
    if code in [200, 202]:
        msg = "Command successfully sent to charger"
    elif code == 400:
        msg = "Command has missing/invalid values"
    elif code == 401:
        msg = "Missing authorization data. Check 'Authorization' header"
    elif code == 403:
        msg = "Forbidden. Authorization set, but access to resource is denied"
    elif code == 415:
        msg = "Payload format is in an unsupported format"
    elif code == 500:
        msg = "Oops! Unexpected internal error. Request has been logged and code monkeys warned"
    elif code == 503:
        msg = "Server gateway cannot reach API. Try again in about a minute..."
    elif code == 504:
        msg = "Unable to deliver commands upstream. End device is not reachable, or a problem with queueing the device command"
    return msg


def convert_to_af(code):
    af = f"Unknown state code: {code}"
    if code in [0, 1]:
        af = "A"
    elif code == 2:
        af = "B"
    elif code == 3:
        af = "C"
    return af


def get_state(charger_id):
    check_access_token()
    url = f"{EASEE_API_BASE}/chargers/{charger_id}/state"
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + ACCESS_TOKEN}
    resp = requests.request("GET", url=url, headers=headers)
    parsed = resp.json()
    logging.debug("State")
    logging.debug(parsed)
    if resp.status_code != 200:
        logging.warning(
            "Response code %s when trying to get_state", resp.status_code)
    return parsed


def publish_state(client, charger):
    state = get_state(charger)
    config = get_config(charger)
    client.publish(
        f"easee2MQTT/{charger}/charging_enabled", config['isEnabled'])
    client.publish(
        f"easee2MQTT/{charger}/charging_current", state['dynamicChargerCurrent'])
    client.publish(f"easee2MQTT/{charger}/chargerOpMode",
                   convert_to_af(state['chargerOpMode']))


def on_message(client, userdata, message):
    logging.info("Message received on topic: %s, payload: %s",
                 message.topic, str(message.payload.decode('utf-8')))
    callback_topic, resp = None, None
    charger = message.topic.split("/")[1]
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + ACCESS_TOKEN}

    if message.topic.split("/")[2] == "charging_enabled":
        url = f"{EASEE_API_BASE}/chargers/{charger}/settings"
        if (str(message.payload.decode("utf-8")).casefold() == "true" or
                str(message.payload.decode("utf-8")).casefold() == "false"):
            data = {
                'enabled': str(message.payload.decode("utf-8")).title()
            }
            resp = requests.post(url, headers=headers, json=data)
            callback_topic = f"easee2MQTT/{charger}/charging_enabled"

        else:
            logging.warning(
                "Couldn't identify payload. 'true' or 'false' is only supported values.")

    elif message.topic.split("/")[2] == "charging_current":
        if float(message.payload.decode('utf-8')) < 33.0:
            url = f"{EASEE_API_BASE}/chargers/{charger}/settings"
            data = {
                "dynamicChargerCurrent": message.payload.decode('utf-8')
            }
            resp = requests.post(url, headers=headers, json=data)
            callback_topic = f"easee2MQTT/{charger}/charging_current"
        else:
            logging.warning("Couldn't publish new charging_current")
    if callback_topic is not None:
        try:
            if message.topic.split("/")[2] != "ping":
                logging.info("Manually publishing setting %s for %s",
                             message.topic.split('/')[2], charger)
                client.publish(callback_topic, message.payload.decode('utf-8'))
        except:
            logging.warning(
                "Couldn't publish manually for message: %s", message)

    if resp is not None:
        try:
            # Log a warning if we still have a status_code
            if resp.status_code in [200, 202]:
                logging.info("Response %s - Payload: %s",
                             response_codes(resp.status_code), message.payload.decode('utf-8'))
            else:
                logging.warning("Failed to send command to charger. Response code %s - %s",
                                resp.status_code, response_codes(resp.status_code))
        except:
            logging.warning(
                "No status_code from received message: %s", message)


def get_config(charger):
    url = f"{EASEE_API_BASE}/chargers/{charger}/config"
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + ACCESS_TOKEN}
    resp = requests.request("GET", url=url, headers=headers)
    parsed = resp.json()
    if resp.status_code != 200:
        logging.warning(
            "Response code %s when trying to get_config", resp.status_code)
    return parsed


def main():
    logging.info("Script is starting. Looking for settings")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "Easee2MQTT")
    logging.debug("MQTT: Connect to %s:%s", mqtt_host, mqtt_port)
    if mqtt_password:
        client.username_pw_set(username=mqtt_username, password=mqtt_password)
    client.connect(mqtt_host, mqtt_port)
    client.loop_start()

    for charger in easee_chargers:
        logging.info("Subscribing to topics for charger %s.", charger)
        client.subscribe(f"easee2MQTT/{charger}/charging_enabled/set")
        client.subscribe(f"easee2MQTT/{charger}/charging_current/set")
    client.on_message = on_message

    try:
        while True:
            for charger in easee_chargers:
                try:
                    logging.debug(
                        "Fetching and publishing latest stats of %s", charger)
                    publish_state(client, charger)
                except Exception as err:
                    logging.error(err)
                    logging.error(
                        "Failed to fetch and publish new stats of %s. Will retry in %s seconds", charger, polling_interval)
            time.sleep(polling_interval)
    except Exception as err:
        logging.error(err)
    client.loop_stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting program")
