import errno
import unittest
from unittest.mock import patch
from radar.server import bind_server

class PortBindingTests(unittest.TestCase):
    def test_retries_occupied_socket_without_port_zero(self):
        calls = []
        def factory(address, handler):
            calls.append(address)
            if len(calls) == 1:
                raise OSError(errno.EADDRINUSE, 'occupied')
            return address
        with patch('radar.server.secrets.randbelow', side_effect=[0, 1]):
            self.assertEqual(bind_server(factory, None), ('127.0.0.1', 49153))
        self.assertEqual(len(calls), 2)

    def test_unexpected_errors_are_not_hidden(self):
        def factory(*args):
            raise OSError(errno.EMFILE, 'too many files')
        with self.assertRaises(OSError) as caught:
            bind_server(factory, None)
        self.assertEqual(caught.exception.errno, errno.EMFILE)

    def test_exhaustion_is_bounded(self):
        def factory(*args):
            raise OSError(errno.EACCES, 'excluded port')
        with self.assertRaisesRegex(OSError, '128 attempts'):
            bind_server(factory, None)
