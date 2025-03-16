## About
This program will transfer data from and control your Easee charging robot via their API to/from a MQTT-broker of your choice. You can read power usage, current state of the charger and it allows you to start/stop charging.

Easee has their API documented [here](https://developer.easee.cloud/reference/post_api-accounts-token). There are plenty of endpoints, data and control here. For this program I've selected a few that seems most relevant and useful to me. If you have other needs let me know and I'll see what can be done.

## Prerequisites
Your charging robot needs to be connected to the internet and you need an account on [easee cloud](https://easee.cloud/). You need to have a MQTT-broker

## Config
Edit values in `config.env`

## Run in Docker/Podman ... 

`docker run -d --env-file config.env ghcr.io/sveba/easee2mqtt:v1.2.0`

## Run in shell
This is a Python program. 

1. Download or clone the repository
2. Install necessary packages from requirements.txt using `pip install -r requirements.txt`
3. Run 
```
source config.env
easee2mqtt.py
``` 

### Published topics
Topic | Content | Unit
--- | --- | ---
{root_topic}/charging_enabled | True if smartcCharging is enabled | bool
{root_topic}/charging_current | Maximum dynamic charging current | INT
{root_topic}/chargerOpMode | True if charging is enabled | bool

### Subscribed topics
You can publish to these topics to control your charger. 

Topic | Payload | Description
--- | --- | ---
{root_topic}/cmnd/charging | Enable smartcCharging | bool
{root_topic}/cmnd/current | Set maximum dynamic charging current | INT

# Usage in evcc.io
The original idea was to use this adapter in EVCC. For this, you need to configure a MQTT-Charger. It works pretty good ;)
```
chargers:
- name: easee
  type: custom
  status:
    source: mqtt
    topic: {root_topic}/chargerOpMode
  enabled:
    source: mqtt
    topic: {root_topic}/charging_enabled
  enable:
    source: mqtt
    topic: {root_topic}/cmnd/charging
  maxcurrent:
    source: mqtt
    topic: {root_topic}/cmnd/current
```
