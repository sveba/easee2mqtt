import asyncio
import aiomqtt
import logging
import os
import sys

from pyeasee import Easee, STATUS

LOGLEVEL = os.environ.get('LOGLEVEL', 'INFO').upper()
polling_interval = int(os.environ.get('POLLING_INTERVAL', 300))

logging.basicConfig(handlers=[logging.StreamHandler(sys.stdout)],
                    level=LOGLEVEL,
                    format="[%(asctime)s] %(levelname)s %(message)s",
                    datefmt='%Y-%m-%d %H:%M:%S')
cur_charger = None


def convert_to_af(code):
    af = "B"
    if code in [STATUS[1], STATUS[0]]:
        af = "A"
    elif code == STATUS[3]:
        af = "C"
    return af


async def setupCharger():
    global cur_charger
    easee_username = os.environ.get('EASEE_USERNAME', None)
    easee_password = os.environ.get('EASEE_PASSWORD', None)
    easee_charger = os.environ.get("EASEE_CHARGER")
    easee = Easee(easee_username, easee_password)

    sites = await easee.get_sites()
    for site in sites:
        logging.debug("Site %s (%s)", site.name, site.id)
        circuits = site.get_circuits()
        for circuit in circuits:
            logging.debug("Circuit %s ", circuit.id)
            chargers = circuit.get_chargers()
            for charger in chargers:
                logging.debug("Charger Config is enabled: %s",
                              charger.__dict__)
                logging.debug("Charger %s (%s).", charger.name, charger.id)
                if charger.id in easee_charger:
                    logging.info("Charger found")
                    cur_charger = charger
                    break


async def refreshCharger(client, mqtt_root_topic):
    while True:
        await publish_state(client, mqtt_root_topic)
        await asyncio.sleep(polling_interval)


async def listen(tg):
    mqtt_host = os.environ.get('MQTT_HOST', None)
    mqtt_port = int(os.environ.get('MQTT_PORT', 1883))
    mqtt_password = os.environ.get('MQTT_PASSWORD', None)
    mqtt_username = os.environ.get('MQTT_USERNAME', None)
    mqtt_root_topic = os.environ.get('MQTT_ROOT_TOPIC', "easee2MQTT")
    logging.info("Connecting to MQTT broker %s:%s", mqtt_host, mqtt_port)
    async with aiomqtt.Client(hostname=mqtt_host, port=mqtt_port, username=mqtt_username, password=mqtt_password) as client:
        tg.create_task(refreshCharger(client, mqtt_root_topic))
        await client.subscribe(f"{mqtt_root_topic}/cmnd/#")
        async for message in client.messages:
            await on_message(message)
            await asyncio.sleep(5)
            await publish_state(client, mqtt_root_topic)


async def on_message(message):
    logging.debug("Message received on topic: %s, payload: %s",
                  message.topic, str(message.payload.decode('utf-8')))
    payload = message.payload.decode('utf-8')

    if message.topic.matches("easee2mqtt/+/cmnd/charging"):
        logging.info("Received command to enable/disable charging")
        await enable(str(payload).casefold() == "true")

    if message.topic.matches("easee2mqtt/+/cmnd/current"):
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

    charger_state = await cur_charger.get_state()
    logging.debug("Charger State: %s", charger_state.__dict__)
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


async def publish_state(client, mqtt_root_topic):
    logging.info("Publishing charger status")

    state = await cur_charger.get_state()
    config = await cur_charger.get_config()
    logging.debug("Charger state: %s", state.__dict__)
    logging.debug("Charger config: %s", config.__dict__)

    await client.publish(f"{mqtt_root_topic}/charging_enabled", state["smartCharging"])
    await client.publish(f"{mqtt_root_topic}/charging_current", state["dynamicChargerCurrent"])
    await client.publish(f"{mqtt_root_topic}/chargerOpMode", convert_to_af(state["chargerOpMode"]))


async def set_current(current: int):
    logging.info("Set dynamic current: %s", current)
    await cur_charger.set_dynamic_charger_current(current)


async def main():
    await setupCharger()
    # Use a task group to manage and await all tasks
    async with asyncio.TaskGroup() as tg:
        tg.create_task(listen(tg))  # Start the listener task

asyncio.run(main())
