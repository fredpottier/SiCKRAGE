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



import re
from urllib.parse import urljoin

from requests.utils import dict_from_cookiejar

import sickrage
from sickrage.core.caches.tv_cache import TVCache
from sickrage.core.helpers import bs4_parser, try_int, convert_size
from sickrage.providers import TorrentProvider


class AlphaRatioProvider(TorrentProvider):
    def __init__(self):
        super(AlphaRatioProvider, self).__init__("AlphaRatio", 'https://alpharatio.cc', True)
        self.username = None
        self.password = None

        self.minseed = None
        self.minleech = None

        self.urls.update({
            'login': '{base_url}/login.php'.format(**self.urls),
            'search': '{base_url}/torrents.php?searchstr=%s%s'.format(**self.urls)
        })

        self.catagories = "&filter_cat[1]=1&filter_cat[2]=1&filter_cat[3]=1&filter_cat[4]=1&filter_cat[5]=1"

        self.proper_strings = ['PROPER', 'REPACK']

        self.cache = TVCache(self, min_time=20)

    def login(self):
        if any(dict_from_cookiejar(self.session.cookies).values()):
            return True

        login_params = {'username': self.username,
                        'password': self.password,
                        'remember_me': 'on',
                        'login': 'submit'}

        try:
            response = self.session.post(self.urls['login'], data=login_params, timeout=30).text
        except Exception:
            sickrage.app.log.warning('Unable to connect to provider')
            return False

        if re.search('Invalid Username/password', response) \
                or re.search('<title>Login :: AlphaRatio.cc</title>', response):
            sickrage.app.log.warning(
                "Invalid username or password. Check your settings")
            return False

        return True

    def search(self, search_strings, age=0, show_id=None, season=None, episode=None, **kwargs):
        results = []

        if not self.login():
            return results

        for mode in search_strings:
            sickrage.app.log.debug("Search Mode: %s" % mode)
            for search_string in search_strings[mode]:
                if mode != 'RSS':
                    sickrage.app.log.debug("Search string: %s " % search_string)

                searchURL = self.urls['search'] % (search_string, self.catagories)

                try:
                    data = self.session.get(searchURL).text
                    results += self.parse(data, mode)
                except Exception:
                    sickrage.app.log.debug("No data returned from provider")

        return results

    def parse(self, data, mode, **kwargs):
        """
        Parse search results from data
        :param data: response data
        :param mode: search mode
        :return: search results
        """

        results = []

        def process_column_header(td):
            result = ''
            if td.a and td.a.img:
                result = td.a.img.get('title', td.a.get_text(strip=True))
            if not result:
                result = td.get_text(strip=True)
            return result

        with bs4_parser(data) as html:
            torrent_table = html.find('table', attrs={'id': 'torrent_table'})
            torrent_rows = torrent_table('tr') if torrent_table else []

            # Continue only if one Release is found
            if len(torrent_rows) < 2:
                sickrage.app.log.debug("Data returned from provider does not contain any torrents")
                return results

            # '', '', 'Name /Year', 'Files', 'Time', 'Size', 'Snatches', 'Seeders', 'Leechers'
            labels = [process_column_header(label) for label in torrent_rows[0]('td')]

            # Skip column headers
            for row in torrent_rows[1:]:
                try:
                    cells = row('td')
                    if len(cells) < len(labels):
                        continue

                    title = cells[labels.index('Name /Year')].find('a', dir='ltr').get_text(strip=True)
                    download = cells[labels.index('Name /Year')].find('a', title='Download')['href']
                    download_url = urljoin(self.urls['base_url'], download)
                    if not all([title, download_url]):
                        continue

                    seeders = try_int(cells[labels.index('Seeders')].get_text(strip=True))
                    leechers = try_int(cells[labels.index('Leechers')].get_text(strip=True))

                    torrent_size = cells[labels.index('Size')].get_text(strip=True)
                    size = convert_size(torrent_size, -1)

                    results += [
                        {'title': title, 'link': download_url, 'size': size, 'seeders': seeders, 'leechers': leechers}
                    ]

                    if mode != 'RSS':
                        sickrage.app.log.debug('Found result: {}'.format(title))
                except Exception:
                    sickrage.app.log.error('Failed parsing provider')

        return results
