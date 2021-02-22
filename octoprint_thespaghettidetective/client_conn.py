import bson
import logging
import json
import socket
import threading
import time
import sys
from collections import deque

from .janus import JANUS_SERVER, JANUS_DATA_PORT

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

class ClientConn:

    def __init__(self, plugin):
        self.plugin = plugin
        self.data_channel_conn = DataChannelConn(JANUS_SERVER, JANUS_DATA_PORT)
        self.seen_refs = deque(maxlen=25)  # contains "last" 25 passthru refs
        self.seen_refs_lock = threading.RLock()

    def on_message_to_plugin(self, msg):
        target = getattr(self.plugin, msg.get('target'))
        func = getattr(target, msg['func'], None)
        if not func:
            return

        ack_ref = msg.get('ref')
        if ack_ref is not None:
            # same msg may arrive through both ws and datachannel
            with self.seen_refs_lock:
                if ack_ref in self.seen_refs:
                    _logger.debug('Got duplicate ref, ignoring msg')
                    return
                # no need to remove item or check fullness
                # as deque manages that when maxlen is set
                self.seen_refs.append(ack_ref)

        ret = func(*(msg.get("args", [])))

        if ack_ref:
            self.plugin.send_ws_msg_to_server(
                {'passthru': {'ref': ack_ref, 'ret': ret}})
            self.send_msg_to_client(
                {'ref': ack_ref, 'ret': ret, '_webrtc': True})

        time.sleep(0.2)  # chnages, such as setting temp will take a bit of time to be reflected in the status. wait for it
        self.plugin.post_printer_status()

    def send_msg_to_client(self, data):
        _logger.debug("Sending to client: \n{}".format(data))
        if __python_version__ == 3:
            raw = json.dumps(data, default=str).encode("utf8")
        else:
            raw = json.dumps(data, encoding='iso-8859-1', default=str)

        self.data_channel_conn.send(raw)

    def close(self):
        self.data_channel_conn.close()

class DataChannelConn(object):

    def __init__(self, addr, port):
        self.addr = addr
        self.port = port
        self.sock = None
        self.sock_lock = threading.RLock()

    def send(self, payload):
        with self.sock_lock:
            if self.sock is None:
                try:
                    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                except OSError as ex:
                    _logger.error('could not open udp socket (%s)' % ex)

            if self.sock is not None:
                try:
                    self.sock.sendto(payload, (self.addr, self.port))
                except socket.error as ex:
                    _logger.error(
                        'could not send to janus datachannel (%s)' % ex)
                except OSError as ex:
                    _logger.error('udp socket might be closed (%s)' % ex)
                    self.sock = None

    def close(self):
        with self.sock_lock:
            self.sock.close()
            self.sock = None