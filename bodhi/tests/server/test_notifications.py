# -*- coding: utf-8 -*-

# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""This test module contains tests for bodhi.server.notifications."""

import unittest

from sqlalchemy import exc
import mock

from bodhi.server import notifications, Session, models
from bodhi.tests.server import base


class TestInit(unittest.TestCase):
    """This test class contains tests for the init() function."""
    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
    @mock.patch('bodhi.server.log.info')
    @mock.patch('fedmsg.config.load_config')
    @mock.patch('fedmsg.init')
    @mock.patch('socket.gethostname', mock.MagicMock(return_value='coolhostname.very.cool.tld'))
    def test_config_passed(self, init, load_config, info):
        """
        Assert that the config from load_config() is passed to init().
        """
        load_config.return_value = {'a': 'config'}
        notifications.init()

        init.assert_called_once_with(a='config', name='bodhi.coolhostname')
        info.assert_called_once_with('fedmsg initialized')

    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': False})
    @mock.patch('bodhi.server.log.warn')
    @mock.patch('bodhi.server.notifications.fedmsg.init')
    def test_fedmsg_disabled(self, init, warn):
        """
        The init() function should log a warning and exit when fedmsg is disabled.
        """
        notifications.init()

        # fedmsg.init() should not have been called
        self.assertEqual(init.call_count, 0)
        warn.assert_called_once_with('fedmsg disabled.  not initializing.')

    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
    @mock.patch('bodhi.server.log.info')
    @mock.patch('fedmsg.init')
    def test_with_active(self, init, info):
        """
        Assert correct behavior with active is not None.
        """
        notifications.init(active=True)

        self.assertEqual(init.call_count, 1)
        init_config = init.mock_calls[0][2]
        self.assertEqual(init_config['active'], True)
        self.assertEqual(init_config['name'], 'relay_inbound')
        self.assertTrue('cert_prefix' not in init_config)
        info.assert_called_once_with('fedmsg initialized')

    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
    @mock.patch('bodhi.server.log.info')
    @mock.patch('fedmsg.init')
    def test_with_cert_prefix(self, init, info):
        """
        Assert correct behavior when cert_prefix is not None.
        """
        notifications.init(cert_prefix='This is a real cert trust me.')

        self.assertEqual(init.call_count, 1)
        init_config = init.mock_calls[0][2]
        self.assertEqual(init_config['cert_prefix'], 'This is a real cert trust me.')
        info.assert_called_once_with('fedmsg initialized')


@mock.patch('bodhi.server.notifications.init')
class TestPublish(base.BaseTestCase):
    """Tests for :func:`bodhi.server.notifications.publish`."""

    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': False})
    def test_publish_off(self, mock_init):
        """Assert publish doesn't populate the info dict when publishing is off."""
        notifications.publish('demo.topic', {'such': 'important'})
        session = Session()
        self.assertEqual(dict(), session.info)
        self.assertEqual(0, mock_init.call_count)

    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
    def test_publish(self, mock_init):
        """Assert publish places the message inside the session info dict."""
        notifications.publish('demo.topic', {'such': 'important'})
        session = Session()
        self.assertIn('fedmsg', session.info)
        self.assertEqual(session.info['fedmsg']['demo.topic'], [{'such': 'important'}])

    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
    @mock.patch('bodhi.server.notifications.fedmsg_is_initialized', mock.Mock(return_value=False))
    def test_publish_sqlalchemy_object(self, mock_init):
        """Assert publish places the message inside the session info dict."""
        Session.remove()
        expected_msg = {
            u'some_package': {
                u'name': u'so good',
                u'type': 'base',
                u'requirements': None,
                u'stack': None,
                u'stack_id': None,
            }
        }
        package = models.Package(name='so good')
        notifications.publish('demo.topic', {'some_package': package})
        session = Session()
        self.assertIn('fedmsg', session.info)
        self.assertEqual(session.info['fedmsg']['demo.topic'], [expected_msg])
        mock_init.assert_called_once_with()

    @mock.patch('bodhi.server.notifications.fedmsg_is_initialized', mock.Mock(return_value=False))
    @mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
    @mock.patch('bodhi.server.notifications.fedmsg.publish')
    def test_publish_force(self, mock_fedmsg_publish, mock_init):
        """Assert publish with the force flag sends the message immediately."""
        notifications.publish('demo.topic', {'such': 'important'}, force=True)
        session = Session()
        self.assertEqual(dict(), session.info)
        mock_fedmsg_publish.assert_called_once_with(
            topic='demo.topic', msg={'such': 'important'})
        mock_init.assert_called_once_with()


@mock.patch.dict('bodhi.server.config.config', {'fedmsg_enabled': True})
@mock.patch('bodhi.server.notifications.init', mock.Mock())
@mock.patch('bodhi.server.notifications.fedmsg.publish')
class TestSendFedmsgsAfterCommit(base.BaseTestCase):

    def test_no_fedmsgs(self, mock_fedmsg_publish):
        """Assert nothing happens if messages are not explicitly published."""
        session = Session()
        session.add(models.Package(name=u'ejabberd'))
        session.commit()

        self.assertEqual(0, mock_fedmsg_publish.call_count)

    def test_commit_aborted(self, mock_fedmsg_publish):
        """Assert that when commits are aborted, messages aren't sent."""
        session = Session()
        session.add(models.Package(name=u'ejabberd'))
        session.commit()

        session.add(models.Package(name=u'ejabberd'))
        notifications.publish('demo.topic', {'new': 'package'})
        self.assertRaises(exc.IntegrityError, session.commit)
        self.assertEqual(0, mock_fedmsg_publish.call_count)

    def test_single_topic_one_message(self, mock_fedmsg_publish):
        """Assert a single message for a single topic is published."""
        session = Session()
        session.add(models.Package(name=u'ejabberd'))
        notifications.publish('demo.topic', {'new': 'package'})
        session.commit()
        mock_fedmsg_publish.assert_called_once_with(
            topic='demo.topic', msg={'new': 'package'})

    def test_empty_commit(self, mock_fedmsg_publish):
        """Assert calling commit on a session with no changes still triggers fedmsgs."""
        # Ensure nothing at all is in our session
        Session.remove()
        session = Session()
        notifications.publish('demo.topic', {'new': 'package'})
        session.commit()
        mock_fedmsg_publish.assert_called_once_with(
            topic='demo.topic', msg={'new': 'package'})

    def test_repeated_commit(self, mock_fedmsg_publish):
        """Assert queued fedmsgs are cleared between commits."""
        session = Session()
        notifications.publish('demo.topic', {'new': 'package'})
        session.commit()
        session.commit()
        mock_fedmsg_publish.assert_called_once_with(
            topic='demo.topic', msg={'new': 'package'})

    def test_single_topic_many_messages(self, mock_fedmsg_publish):
        """Assert many messages for a single topic are sent."""
        session = Session()
        notifications.publish('demo.topic', {'new': 'package'})
        notifications.publish('demo.topic', {'newer': 'packager'})
        session.commit()
        self.assertEqual(2, mock_fedmsg_publish.call_count)
        mock_fedmsg_publish.assert_any_call(
            topic='demo.topic', msg={'new': 'package'})
        mock_fedmsg_publish.assert_any_call(
            topic='demo.topic', msg={'newer': 'packager'})

    def test_multiple_topics(self, mock_fedmsg_publish):
        """Assert messages with different topics are sent."""
        session = Session()
        notifications.publish('demo.topic', {'new': 'package'})
        notifications.publish('other.topic', {'newer': 'packager'})
        session.commit()
        self.assertEqual(2, mock_fedmsg_publish.call_count)
        mock_fedmsg_publish.assert_any_call(
            topic='demo.topic', msg={'new': 'package'})
        mock_fedmsg_publish.assert_any_call(
            topic='other.topic', msg={'newer': 'packager'})
