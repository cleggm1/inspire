#!/usr/bin/env python
# -*- coding: utf-8 -*-
##
## This file is part of INSPIRE.
## Copyright (C) 2015, 2016 CERN.
##
## INSPIRE is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or (at your option) any later version.
##
## INSPIRE is distributed in the hope that it will be useful, but
## WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with INSPIRE; if not, write to the Free Software Foundation, Inc.,
## 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

"""Continuously synchronizes to Labs via REDIS"""

import datetime
import time
import tarfile
import os

from invenio.config import CFG_TMPSHAREDDIR

try:
    from invenio.config import CFG_REDIS_HOST_LABS
except ImportError:
    CFG_REDIS_HOST_LABS = None

from invenio.dbquery import run_sql

from invenio.dateutils import get_time_estimator

from invenio.bibformat_engine import format_record

from invenio.bibtask import task_update_progress, write_message, task_sleep_now_if_required

from invenio.intbitset import intbitset

from invenio.webuser import collect_user_info, get_uid_from_email, get_email_from_username

import redis

import gzip
import zlib

ADMIN_USER_INFO = collect_user_info(get_uid_from_email(get_email_from_username('admin')))

CFG_OUTPUT_PATH = "/afs/cern.ch/project/inspire/PROD/var/tmp-shared/prodsync"

def bst_prodsync(method='afs'):
    """
    Synchronize to either 'afs' or 'redis'
    """
    if not CFG_REDIS_HOST_LABS:
        method = 'afs'
    write_message("Prodsync started using %s method" % method)
    now = datetime.datetime.now()
    future_lastrun = now.strftime('%Y-%m-%d %H:%M:%S')
    lastrun_path = os.path.join(CFG_TMPSHAREDDIR, 'prodsync_%s_lastrun.txt' % method)
    try:
        last_run = open(lastrun_path).read().strip()
        write_message("Syncing records modified since %s" % last_run)
        modified_records = intbitset(run_sql("SELECT id FROM bibrec WHERE modification_date>=%s", (last_run, )))
        for citee, citer in run_sql("SELECT citee, citer FROM rnkCITATIONDICT WHERE last_updated>=%s", (last_run, )):
            modified_records.add(citer)
        modified_records |= intbitset(run_sql("SELECT bibrec FROM aidPERSONIDPAPERS WHERE last_updated>=%s", (last_run, )))
    except IOError:
        # Default to the epoch
        modified_records = intbitset(run_sql("SELECT id FROM bibrec"))
        write_message("Syncing all records")

    if not modified_records:
        write_message("Nothing to do")
        return True
    if method == 'redis':
        r = redis.StrictRedis.from_url(CFG_REDIS_HOST_LABS)
    else:
        write_message("Appeding output to %s" % CFG_OUTPUT_PATH)
        prodsyncname = CFG_OUTPUT_PATH + now.strftime("%Y%m%d%H%M%S") + '.xml.gz'
        r = gzip.open(prodsyncname, "w")
        print >> r, '<collection xmlns="http://www.loc.gov/MARC21/slim">'
    tot = len(modified_records)
    time_estimator = get_time_estimator(tot)
    write_message("Adding %s new or modified records" % tot)
    for i, recid in enumerate(modified_records):
        if method == 'redis':
            r.rpush('legacy_records', zlib.compress(format_record(recid, 'xme', user_info=ADMIN_USER_INFO)[0]))
            # Client should simply use http://redis.io/commands/lpop
        else:
            print >> r, format_record(recid, 'xme', user_info=ADMIN_USER_INFO)[0]
        time_estimation = time_estimator()[1]
        if (i + 1) % 100 == 0:
            task_update_progress("%s (%s%%) -> %s" % (recid, (i + 1) * 100 / tot, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time_estimation))))
            if method == 'afs':
                r.flush()
            task_sleep_now_if_required()
    write_message("Pushed %s records" % tot)
    if method == 'afs':
        print >> r, '</collection>'
        r.close()
        prodsync_tarname = CFG_OUTPUT_PATH + '.tar'
        write_message("Adding %s to %s" % (prodsyncname, prodsync_tarname))
        prodsync_tar = tarfile.open(prodsync_tarname, 'a')
        prodsync_tar.add(prodsyncname)
        prodsync_tar.close()
        os.remove(prodsyncname)
    open(lastrun_path, "w").write(future_lastrun)
    write_message("DONE!")
