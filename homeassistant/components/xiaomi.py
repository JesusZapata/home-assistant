"""Support for Xiaomi Gateways."""
import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import discovery
from homeassistant.helpers.entity import Entity
from homeassistant.const import ATTR_BATTERY_LEVEL, EVENT_HOMEASSISTANT_STOP


REQUIREMENTS = ['https://github.com/Danielhiversen/PyXiaomiGateway/archive/'
                '06a96433974c56d02aec7c5e3174b1fd5e133008.zip#'
                'PyXiaomiGateway==0.1.0']

ATTR_RINGTONE_ID = 'ringtone_id'
ATTR_GW_SID = 'gw_sid'
ATTR_RINGTONE_VOL = 'ringtone_vol'
DOMAIN = 'xiaomi'
CONF_GATEWAYS = 'gateways'
CONF_INTERFACE = 'interface'
CONF_DISCOVERY_RETRY = 'discovery_retry'
PY_XIAOMI_GATEWAY = "xiaomi_gw"
XIAOMI_COMPONENTS = ['binary_sensor', 'sensor', 'switch', 'light', 'cover']

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_GATEWAYS, default=[{"sid": None, "key": None}]):
            cv.ensure_list,
        vol.Optional(CONF_INTERFACE, default='any'): cv.string,
        vol.Optional(CONF_DISCOVERY_RETRY, default=3): cv.positive_int
    })
}, extra=vol.ALLOW_EXTRA)

_LOGGER = logging.getLogger(__name__)


def setup(hass, config):
    """Set up the Xiaomi component."""
    gateways = config[DOMAIN][CONF_GATEWAYS]
    interface = config[DOMAIN][CONF_INTERFACE]
    discovery_retry = config[DOMAIN][CONF_DISCOVERY_RETRY]

    for gateway in gateways:
        sid = gateway['sid']

        if sid is not None:
            gateway['sid'] = gateway['sid'].replace(":", "").lower()

        key = gateway['key']
        if key is None:
            _LOGGER.warning('Gateway Key is not provided.'
                            ' Controlling gateway device'
                            ' will not be possible.')
        elif len(key) != 16:
            _LOGGER.error('Invalid key %s. Key must be 16 characters', key)
            return False

    from PyXiaomiGateway import PyXiaomiGateway
    hass.data[PY_XIAOMI_GATEWAY] = PyXiaomiGateway(hass.add_job, gateways,
                                                   interface)

    _LOGGER.info("Expecting %s gateways", len(gateways))
    for _ in range(discovery_retry):
        _LOGGER.info('Discovering Xiaomi Gateways (Try %s)', _ + 1)
        hass.data[PY_XIAOMI_GATEWAY].discover_gateways()
        if len(hass.data[PY_XIAOMI_GATEWAY].gateways) >= len(gateways):
            break

    if not hass.data[PY_XIAOMI_GATEWAY].gateways:
        _LOGGER.error("No gateway discovered")
        return False
    hass.data[PY_XIAOMI_GATEWAY].listen()
    _LOGGER.info("Listening for broadcast")

    def stop_xiaomi(event):
        """Stop Xiaomi Socket."""
        _LOGGER.info("Shutting down Xiaomi Hub.")
        hass.data[PY_XIAOMI_GATEWAY].stop_listen()
    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, stop_xiaomi)

    for component in XIAOMI_COMPONENTS:
        discovery.load_platform(hass, component, DOMAIN, {}, config)

    def play_ringtone_service(call):
        """Service to play ringtone through Gateway."""
        if call.data.get(ATTR_RINGTONE_ID) is None \
                or call.data.get(ATTR_GW_SID) is None:
            _LOGGER.error("Mandatory parameters is not specified.")
            return

        ring_id = int(call.data.get(ATTR_RINGTONE_ID))
        if ring_id in [9, 14-19]:
            _LOGGER.error('Specified mid: %s is not defined in gateway.',
                          ring_id)
            return

        ring_vol = call.data.get(ATTR_RINGTONE_VOL)
        if ring_vol is None:
            ringtone = {'mid': ring_id}
        else:
            ringtone = {'mid': ring_id, 'vol': int(ring_vol)}

        gw_sid = call.data.get(ATTR_GW_SID)

        for (_, gateway) in hass.data[PY_XIAOMI_GATEWAY].gateways.items():
            if gateway.sid == gw_sid:
                gateway.write_to_hub(gateway.sid, **ringtone)
                break
        else:
            _LOGGER.error('Unknown gateway sid: %s was specified.', gw_sid)

    def stop_ringtone_service(call):
        """Service to stop playing ringtone on Gateway."""
        gw_sid = call.data.get(ATTR_GW_SID)
        if gw_sid is None:
            _LOGGER.error("Mandatory parameter (%s) is not specified.",
                          ATTR_GW_SID)
            return

        for (_, gateway) in hass.data[PY_XIAOMI_GATEWAY].gateways.items():
            if gateway.sid == gw_sid:
                ringtone = {'mid': 10000}
                gateway.write_to_hub(gateway.sid, **ringtone)
                break
        else:
            _LOGGER.error('Unknown gateway sid: %s was specified.', gw_sid)

    hass.services.async_register(DOMAIN, 'play_ringtone',
                                 play_ringtone_service,
                                 description=None, schema=None)
    hass.services.async_register(DOMAIN, 'stop_ringtone',
                                 stop_ringtone_service,
                                 description=None, schema=None)
    return True


class XiaomiDevice(Entity):
    """Representation a base Xiaomi device."""

    def __init__(self, device, name, xiaomi_hub):
        """Initialize the xiaomi device."""
        self._sid = device['sid']
        self._name = '{}_{}'.format(name, self._sid)
        self.xiaomi_hub = xiaomi_hub
        self.parse_data(device['data'])
        self._device_state_attributes = {}
        self.parse_voltage(device['data'])
        xiaomi_hub.ha_devices[self._sid].append(self)

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def should_poll(self):
        """Poll update device status."""
        return False

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return self._device_state_attributes

    def push_data(self, data):
        """Push from Hub."""
        _LOGGER.debug("PUSH >> %s: %s", self, data)

        if self.parse_data(data) or self.parse_voltage(data):
            self.schedule_update_ha_state()

    def parse_voltage(self, data):
        """Parse battery level data sent by gateway."""
        if 'voltage' not in data:
            return False
        max_volt = 3300
        min_volt = 2800
        voltage = data['voltage']
        voltage = min(voltage, max_volt)
        voltage = max(voltage, min_volt)
        percent = ((voltage - min_volt) / (max_volt - min_volt)) * 100
        self._device_state_attributes[ATTR_BATTERY_LEVEL] = round(percent)
        return True

    def parse_data(self, data):
        """Parse data sent by gateway."""
        raise NotImplementedError()