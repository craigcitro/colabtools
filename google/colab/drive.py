# Copyright 2018 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Colab-specific Google Drive integration."""

from __future__ import absolute_import as _
from __future__ import division as _
from __future__ import print_function as _

import getpass as _getpass
import os as _os
import re as _re
import socket as _socket
import sys as _sys
import uuid as _uuid

import pexpect as _pexpect

__all__ = ['mount']


def mount(mountpoint, force_remount=False):
  """Mount your Google Drive at the specified mountpoint path."""

  mountpoint = _os.path.expanduser(mountpoint)
  # If we've already mounted drive at the specified mountpoint, exit now.
  already_mounted = _os.path.isdir(_os.path.join(mountpoint, 'My Drive'))
  if not force_remount and already_mounted:
    print('Drive already mounted at {}; to attempt to forcibly remount, '
          'call drive.mount("{}", force_remount=True).'.format(
              mountpoint, mountpoint))
    return
  home = _os.environ['HOME']
  root_dir = _os.path.realpath(
      _os.path.join(_os.environ['CLOUDSDK_CONFIG'], '../..'))
  inet_family = 'IPV4_ONLY'
  dev = '/dev/fuse'
  path = '/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:.'
  if len(root_dir) > 1:
    home = _os.path.join(root_dir, home)
    inet_family = 'IPV6_ONLY'
    fum = _os.environ['HOME'].split('mount')[0] + '/mount/alloc/fusermount'
    dev = fum + '/dev/fuse'
    path = path + ':' + fum + '/bin'
  config_dir = _os.path.join(home, '.config', 'Google')
  try:
    _os.makedirs(config_dir)
  except OSError:
    if not _os.path.isdir(config_dir):
      raise ValueError('{} must be a directory if present'.format(config_dir))

  # Launch an intermediate bash inside of which drive is launched, so that
  # after auth is done we can daemonize drive with its stdout/err no longer
  # being captured by pexpect. Otherwise buffers will eventually fill up and
  # drive may hang, because pexpect doesn't have a .startDiscardingOutput()
  # call (https://github.com/pexpect/pexpect/issues/54).
  prompt = u'root@{}-{}: '.format(_socket.gethostname(), _uuid.uuid4().hex)
  d = _pexpect.spawn(
      '/bin/bash',
      args=['--noediting'],
      timeout=120,
      maxread=int(1e6),
      encoding='utf-8',
      env={
          'HOME': home,
          'FUSE_DEV_NAME': dev,
          'PATH': path
      })
  if mount._DEBUG:  # pylint:disable=protected-access
    d.logfile_read = _sys.stdout
  d.sendline('export PS1="{}"'.format(prompt))
  d.expect(prompt)  # The echoed input above.
  d.expect(prompt)  # The new prompt.
  # Robustify to previously-running copies of drive. Don't only [pkill -9]
  # because that leaves enough cruft behind in the mount table that future
  # operations fail with "Transport endpoint is not connected".
  d.sendline('umount -f {mnt} || umount {mnt}; pkill -9 -x drive'.format(
      mnt=mountpoint))
  # Wait for above to be received, using the next prompt.
  d.expect(u'pkill')  # Echoed command.
  d.expect(prompt)
  # Only check the mountpoint after potentially unmounting/pkill'ing above.
  try:
    if _os.path.islink(mountpoint):
      raise ValueError('Mountpoint must not be a symlink')
    if _os.path.isdir(mountpoint) and _os.listdir(mountpoint):
      raise ValueError('Mountpoint must not already contain files')
    if not _os.path.isdir(mountpoint) and _os.path.exists(mountpoint):
      raise ValueError('Mountpoint must either be a directory or not exist')
  except:
    d.terminate(force=True)
    raise

  # Watch for success.
  success = u'google.colab.drive MOUNTED'
  success_watcher = (
      '( while `sleep 0.5`; do if [[ -d "{m}" && "$(ls -A {m})" != "" ]]; '
      'then echo "{s}"; break; fi; done ) &').format(
          m=mountpoint, s=success)
  d.sendline(success_watcher)
  d.expect(prompt)  # Eat the match of the input command above being echoed.
  drive_dir = _os.path.join(root_dir, 'opt/google/drive')
  d.sendline(
      ('{d}/drive --features=opendir_timeout_ms:15000,virtual_folders:true '
       '--inet_family=' + inet_family + ' '
       '--preferences=trusted_root_certs_file_path:'
       '{d}/roots.pem,mount_point_path:{mnt} --console_auth').format(
           d=drive_dir, mnt=mountpoint))

  while True:
    case = d.expect([
        success, prompt,
        _re.compile(u'(Go to this URL in a browser: https://.*)\r\n')
    ])
    if case == 0:
      break
    elif case == 1:
      # Prompt appearing here means something went wrong with the drive binary.
      d.terminate(force=True)
      raise ValueError('mount failed')
    elif case == 2:
      # Not already authorized, so do the authorization dance.
      prompt = d.match.group(1) + '\n\nEnter your authorization code:\n'
      d.send(_getpass.getpass(prompt) + '\n')
  d.sendcontrol('z')
  d.expect(u'Stopped')
  d.sendline('bg; disown; exit')
  d.expect(_pexpect.EOF)
  assert not d.isalive()
  assert d.exitstatus == 0
  print('Mounted at {}'.format(mountpoint))


mount._DEBUG = False  # pylint:disable=protected-access
