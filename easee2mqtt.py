import asyncio
import logging
import os
import sys

import aiomqtt
from pyeasee import Easee, STATUS


LOGLEVEL = os.environ.get("LOGLEVEL", "INFO").upper()
polling_interval = int(os.environ.get("POLLING_INTERVAL", 300))
mqtt_host = os.environ.get("MQTT_HOST", None)
mqtt_port = int(os.environ.get("MQTT_PORT", 1883))
mqtt_password = os.environ.get("MQTT_PASSWORD", None)
mqtt_username = os.environ.get("MQTT_USERNAME", None)
mqtt_root_topic = os.environ.get("MQTT_ROOT_TOPIC", "easee2MQTT").strip("/")
cur_charger = None
cur_charger_serial = None
STATE_OBSERVATIONS = {
    48: "dynamicChargerCurrent",
    102: "smartCharging",
    109: "chargerOpMode",
}

logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)],
                    level=LOGLEVEL,
                    format="[%(asctime)s] %(levelname)s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")


def mqtt_topic(suffix):
    return f"{mqtt_root_topic}/{suffix}"


def command_topic(command):
    return mqtt_topic(f"cmnd/{command}")


def convert_to_af(code):
    af = "B"
    if code in [STATUS[1], STATUS[0]]:
        af = "A"
    elif code == STATUS[3]:
        af = "C"
    return af


def _observation_id(observation):
    return observation.get("id") or observation.get("observationId") or observation.get("ID")


def _observation_value(observation):
    return observation.get("value", observation.get("Value"))


def _typed_observation_value(observation_id, value):
    if observation_id == 102:
        return str(value).casefold() == "true" if isinstance(value, str) else value
    if observation_id == 109:
        return STATUS[int(value)]
    if observation_id == 48:
        return float(value)
    return value


def observations_to_state(observations):
    if isinstance(observations, dict):
        observations = observations.get("data") or observations.get("observations") or []
    elif observations is None:
        observations = []

    return {
        STATE_OBSERVATIONS[observation_id]: _typed_observation_value(observation_id, _observation_value(observation))
        for observation in observations
        if (observation_id := _observation_id(observation)) in STATE_OBSERVATIONS
    }


def find_serial_number(details):
    if isinstance(details, dict):
        for key in ("serialNumber", "serialNo", "serial", "chargerSerialNumber"):
            if details.get(key):
                return str(details[key])
        for value in details.values():
            serial = find_serial_number(value)
            if serial:
                return serial
    if isinstance(details, list):
        for value in details:
            serial = find_serial_number(value)
            if serial:
                return serial


async def get_charger_serial():
    global cur_charger_serial
    if cur_charger_serial is None:
        details = await (await cur_charger.easee.get(f"/api/chargers/{cur_charger.id}/details")).json()
        cur_charger_serial = find_serial_number(details)
        if not cur_charger_serial:
            raise ValueError(f"Serial number missing from details for charger {cur_charger.id}")
    return cur_charger_serial


async def get_charger_state():
    ids = ",".join(str(observation_id) for observation_id in STATE_OBSERVATIONS)
    serial = await get_charger_serial()
    observations = await (await cur_charger.easee.get(f"/state/{serial}/observations?ids={ids}")).json()
    return observations_to_state(observations)




async def setupCharger():
    global cur_charger
    easee_username = os.environ.get("EASEE_USERNAME", None)
    easee_password = os.environ.get("EASEE_PASSWORD", None)
    easee_charger = os.environ.get("EASEE_CHARGER") or os.environ.get("EASEE_CHARGERS", "").split(",")[0]
    easee = Easee(easee_username, easee_password)

    sites = await easee.get_sites()
    for site in sites:
        logging.debug("Site %s (%s)", site.name, site.id)
        circuits = site.get_circuits()
        for circuit in circuits:
            logging.debug("Circuit %s ", circuit.id)
            chargers = circuit.get_chargers()
            for charger in chargers:
                logging.debug("Charger Config is enabled: %s", charger.__dict__)
                logging.debug("Charger %s (%s).", charger.name, charger.id)
                if charger.id == easee_charger:
                    logging.info("Charger found")
                    cur_charger = charger
                    return
    raise ValueError(f"Charger {easee_charger} not found")


async def refreshCharger(client):
    while True:
        await publish_state(client)
        await asyncio.sleep(polling_interval)


async def listen(tg):
    logging.info("Connecting to MQTT broker %s:%s", mqtt_host, mqtt_port)
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port, username=mqtt_username, password=mqtt_password) as client:
        tg.create_task(refreshCharger(client))
        await client.subscribe(command_topic("#"))
        async for message in client.messages:
            await on_message(message)
            await asyncio.sleep(5)
            await publish_state(client)


async def on_message(message):
    logging.debug("Message received on topic: %s, payload: %s",
                  message.topic, str(message.payload.decode("utf-8")))
    payload = message.payload.decode("utf-8")

    if message.topic.matches(command_topic("charging")):
        logging.info("Received command to enable/disable charging")
        await enable(str(payload).casefold() == "true")

    if message.topic.matches(command_topic("current")):
        logging.info("Received command to set current")
        if int(payload) < 33:
            await set_current(int(payload))


async def enable(enable: bool):
    charger_config = await cur_charger.get_config()
    logging.debug("Charger Config: %s", charger_config.__dict__)
    enabling_required = enable and not charger_config["isEnabled"]

    if enabling_required:
        logging.debug("Enabling charger")
        await cur_charger.enable_charger(enable)

    charger_state = await get_charger_state()
    logging.debug("Charger State: %s", charger_state)
    if charger_state["chargerOpMode"] in [STATUS[1], STATUS[7]]:
        logging.warning("Charger can not be paused/resumed in this state")
        return

    if enable:
        logging.info("Resume charger")
        await cur_charger.smart_charging(True)
        await cur_charger.resume()
    else:
        logging.info("Pause charger")
        await cur_charger.smart_charging(False)
        await cur_charger.pause()


async def publish_state(client):
    logging.info("Publishing charger status")

    state = await get_charger_state()
    config = await cur_charger.get_config()
    logging.debug("Charger state: %s", state)
    logging.debug("Charger config: %s", config.__dict__)

    await client.publish(mqtt_topic("charging_enabled"), state["smartCharging"])
    await client.publish(mqtt_topic("charging_current"), state["dynamicChargerCurrent"])
    await client.publish(mqtt_topic("chargerOpMode"), convert_to_af(state["chargerOpMode"]))


async def set_current(current: int):
    logging.info("Set dynamic current: %s", current)
    await cur_charger.set_dynamic_charger_current(current)


async def main():
    await setupCharger()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(listen(tg))


if __name__ == "__main__":
    asyncio.run(main())
