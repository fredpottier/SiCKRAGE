#!/usr/bin/env python3
# ##############################################################################
#  Author: echel0n <echel0n@sickrage.ca>
#  URL: https://sickrage.ca/
#  Git: https://git.sickrage.ca/SiCKRAGE/sickrage.git
#  -
#  This file is part of SiCKRAGE.
#  -
#  SiCKRAGE is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#  -
#  SiCKRAGE is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#  -
#  You should have received a copy of the GNU General Public License
#  along with SiCKRAGE.  If not, see <http://www.gnu.org/licenses/>.
# ##############################################################################


import datetime
import threading
import unittest

import sickrage
import tests
from sickrage.core.common import UNAIRED
from sickrage.core.tv.episode import TVEpisode
from sickrage.core.tv.show import TVShow
from sickrage.core.databases.main import MainDB


class DBBasicTests(tests.SiCKRAGETestDBCase):
    @MainDB.with_session
    def setUp(self, session=None):
        super(DBBasicTests, self).setUp()
        show = TVShow(**{'indexer': 1, 'indexer_id': 0o0001, 'lang': 'en'})
        session.add(show)
        session.commit()

        ep = TVEpisode(**{'showid': show.indexer_id, 'indexer': 1, 'season': 1, 'episode': 1, 'location': ''})
        session.add(ep)
        ep.indexer_id = 1
        ep.name = "test episode 1"
        ep.airdate = datetime.date.fromordinal(733832)
        ep.status = UNAIRED
        session.commit()

        ep = TVEpisode(**{'showid': show.indexer_id, 'indexer': 1, 'season': 1, 'episode': 2, 'location': ''})
        session.add(ep)
        ep.indexer_id = 2
        ep.name = "test episode 2"
        ep.airdate = datetime.date.fromordinal(733832)
        ep.status = UNAIRED
        session.commit()

        ep = TVEpisode(**{'showid': show.indexer_id, 'indexer': 1, 'season': 1, 'episode': 3, 'location': ''})
        session.add(ep)
        ep.indexer_id = 3
        ep.name = "test episode 3"
        ep.airdate = datetime.date.fromordinal(733832)
        ep.status = UNAIRED
        session.commit()

    @MainDB.with_session
    def test_unaired(self, session=None):
        count = 0

        for episode_obj in session.query(TVEpisode):
            if all([episode_obj.status == UNAIRED, episode_obj.season > 0, episode_obj.airdate > datetime.date.min]):
                count += 1

                ep = TVEpisode(**{'indexer': 1, 'episode': episode_obj.episode})
                ep.indexer_id = episode_obj.episode
                ep.name = "test episode {}".format(episode_obj.episode)
                ep.airdate = datetime.date.fromordinal(733832)
                ep.status = UNAIRED

        self.assertEqual(count, 3)

    @MainDB.with_session
    def test_multithread(self, session=None):
        threads = []

        for __ in range(1, 200):
            threads.append(threading.Thread(target=lambda: session.query(TVEpisode).all()))

        for t in threads:
            t.start()

        for t in threads:
            t.join()

if __name__ == '__main__':
    print("==================")
    print("STARTING - DB TESTS")
    print("==================")
    print("######################################################################")
    unittest.main()
