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
import os
import time
import traceback

import sickrage
from sickrage.core.common import WANTED
from sickrage.core.databases.main import MainDB
from sickrage.core.exceptions import CantRefreshShowException, CantRemoveShowException, CantUpdateShowException, EpisodeDeletedException, \
    MultipleShowObjectsException
from sickrage.core.queues import SRQueue, SRQueueItem, SRQueuePriorities
from sickrage.core.scene_numbering import xem_refresh, get_xem_numbering_for_show
from sickrage.core.traktapi import TraktAPI
from sickrage.core.tv.show import TVShow
from sickrage.core.tv.show.helpers import find_show
from sickrage.indexers import IndexerApi
from sickrage.indexers.exceptions import indexer_attributenotfound, indexer_error, indexer_exception


class ShowQueue(SRQueue):
    def __init__(self):
        SRQueue.__init__(self, "SHOWQUEUE")

    @property
    def loading_show_list(self):
        return [{'name': x.show_name, 'indexer_id': x.indexer_id} for x in self.queue_items if x.is_loading]

    def _is_in_queue(self, indexer_id):
        return indexer_id in [x.indexer_id for x in self.queue_items]

    def _is_being(self, indexer_id, actions):
        return indexer_id in [x.indexer_id for x in self.queue_items if x.action_id in actions]

    def is_being_removed(self, indexer_id):
        return self._is_being(indexer_id, [ShowQueueActions.REMOVE])

    def is_being_added(self, indexer_id):
        return self._is_being(indexer_id, [ShowQueueActions.ADD])

    def is_being_updated(self, indexer_id):
        return self._is_being(indexer_id, [ShowQueueActions.UPDATE, ShowQueueActions.FORCEUPDATE])

    def is_being_refreshed(self, indexer_id):
        return self._is_being(indexer_id, [ShowQueueActions.REFRESH])

    def is_being_renamed(self, indexer_id):
        return self._is_being(indexer_id, [ShowQueueActions.RENAME])

    def is_being_subtitled(self, indexer_id):
        return self._is_being(indexer_id, [ShowQueueActions.SUBTITLE])

    def update_show(self, indexer_id, indexer_update_only=False, force=False):
        show_obj = find_show(indexer_id)

        if self.is_being_added(indexer_id):
            raise CantUpdateShowException("{} is still being added, please wait until it is finished before trying to update.".format(show_obj.name))

        if self.is_being_updated(indexer_id):
            raise CantUpdateShowException("{} is already being updated, can't update again until it's done.".format(show_obj.name))

        if self.is_being_updated(indexer_id):
            raise CantUpdateShowException("{} is in the process of being updated, can't update again until it's done.".format(show_obj.name))

        if force:
            sickrage.app.io_loop.add_callback(self.put, QueueItemForceUpdate(indexer_id, indexer_update_only))
        else:
            sickrage.app.io_loop.add_callback(self.put, QueueItemUpdate(indexer_id, indexer_update_only))

    def refresh_show(self, indexer_id, force=False):
        show_obj = find_show(indexer_id)

        if (self.is_being_refreshed(indexer_id) or self.is_being_refreshed(indexer_id)) and not force:
            raise CantRefreshShowException("This show is already being refreshed or queued to be refreshed, skipping this request.")

        if show_obj.paused and not force:
            sickrage.app.log.debug('Skipping show [{}] because it is paused.'.format(show_obj.name))
            return

        sickrage.app.log.debug("Queueing show refresh for {}".format(show_obj.name))

        sickrage.app.io_loop.add_callback(self.put, QueueItemRefresh(indexer_id, force=force))

    def rename_show_episodes(self, indexer_id):
        sickrage.app.io_loop.add_callback(self.put, QueueItemRename(indexer_id))

    def download_subtitles(self, indexer_id):
        sickrage.app.io_loop.add_callback(self.put, QueueItemSubtitle(indexer_id))

    def add_show(self, indexer, indexer_id, showDir, default_status=None, quality=None, flatten_folders=None,
                 lang=None, subtitles=None, sub_use_sr_metadata=None, anime=None, scene=None, paused=None,
                 blacklist=None, whitelist=None, default_status_after=None, skip_downloaded=None):

        if lang is None:
            lang = sickrage.app.config.indexer_default_language

        sickrage.app.io_loop.add_callback(self.put, QueueItemAdd(indexer=indexer,
                                                                 indexer_id=indexer_id,
                                                                 showDir=showDir,
                                                                 default_status=default_status,
                                                                 quality=quality,
                                                                 flatten_folders=flatten_folders,
                                                                 lang=lang,
                                                                 subtitles=subtitles,
                                                                 sub_use_sr_metadata=sub_use_sr_metadata,
                                                                 anime=anime,
                                                                 scene=scene,
                                                                 paused=paused,
                                                                 blacklist=blacklist,
                                                                 whitelist=whitelist,
                                                                 default_status_after=default_status_after,
                                                                 skip_downloaded=skip_downloaded))

    def remove_show(self, indexer_id, full=False):
        show_obj = find_show(indexer_id)

        if not show_obj:
            raise CantRemoveShowException('Failed removing show: Show does not exist')
        elif not hasattr(show_obj, 'indexer_id'):
            raise CantRemoveShowException('Failed removing show: Show does not have an indexer id')
        elif self._is_being(show_obj.indexer_id, (ShowQueueActions.REMOVE,)):
            raise CantRemoveShowException("{} is already queued to be removed".format(show_obj))

        # remove other queued actions for this show.
        [self.remove(x) for x in self.queue_items if indexer_id == x.indexer_id]

        sickrage.app.io_loop.add_callback(self.put, QueueItemRemove(indexer_id=indexer_id, full=full))


class ShowQueueActions(object):
    def __init__(self):
        pass

    REFRESH = 1
    ADD = 2
    UPDATE = 3
    FORCEUPDATE = 4
    RENAME = 5
    SUBTITLE = 6
    REMOVE = 7

    names = {
        REFRESH: 'Refresh',
        ADD: 'Add',
        UPDATE: 'Update',
        FORCEUPDATE: 'Force Update',
        RENAME: 'Rename',
        SUBTITLE: 'Subtitle',
        REMOVE: 'Remove Show'
    }


class ShowQueueItem(SRQueueItem):
    """
    Represents an item in the queue waiting to be executed

    Can be either:
    - show being added (may or may not be associated with a show object)
    - show being refreshed
    - show being updated
    - show being force updated
    - show being subtitled
    """

    def __init__(self, indexer_id, action_id):
        super(ShowQueueItem, self).__init__(ShowQueueActions.names[action_id], action_id)
        self.indexer_id = indexer_id

    def is_in_queue(self):
        return self in sickrage.app.show_queue.queue_items

    @property
    def show_name(self):
        show_obj = find_show(self.indexer_id)
        return show_obj.name if show_obj else str(self.indexer_id)

    @property
    def is_loading(self):
        return False


class QueueItemAdd(ShowQueueItem):
    def __init__(self, indexer, indexer_id, showDir, default_status, quality, flatten_folders, lang, subtitles, sub_use_sr_metadata, anime, scene, paused,
                 blacklist, whitelist, default_status_after, skip_downloaded):
        super(QueueItemAdd, self).__init__(None, ShowQueueActions.ADD)

        self.indexer = indexer
        self.indexer_id = indexer_id
        self.showDir = showDir
        self.default_status = default_status
        self.quality = quality
        self.flatten_folders = flatten_folders
        self.lang = lang
        self.subtitles = subtitles
        self.sub_use_sr_metadata = sub_use_sr_metadata
        self.anime = anime
        self.scene = scene
        self.paused = paused
        self.blacklist = blacklist
        self.whitelist = whitelist
        self.default_status_after = default_status_after
        self.skip_downloaded = skip_downloaded
        self.priority = SRQueuePriorities.HIGH

    @property
    def show_name(self):
        """
        Returns the show name if there is a show object created, if not returns
        the dir that the show is being added to.
        """

        show_obj = find_show(self.indexer_id)
        return show_obj.name if show_obj else os.path.basename(self.showDir)

    @property
    def is_loading(self):
        """
        Returns True if we've gotten far enough to have a show object, or False
        if we still only know the folder name.
        """
        if find_show(self.indexer_id):
            return True

    @MainDB.with_session
    def run(self, session=None):
        start_time = time.time()

        sickrage.app.log.info("Started adding show {} from show dir: {}".format(self.show_name, self.showDir))

        index_name = IndexerApi(self.indexer).name

        # make sure the Indexer IDs are valid
        try:

            lINDEXER_API_PARMS = IndexerApi(self.indexer).api_params.copy()
            lINDEXER_API_PARMS['cache'] = False
            lINDEXER_API_PARMS['language'] = self.lang or sickrage.app.config.indexer_default_language

            sickrage.app.log.info("{}: {}".format(index_name, repr(lINDEXER_API_PARMS)))

            t = IndexerApi(self.indexer).indexer(**lINDEXER_API_PARMS)

            try:
                s = t[self.indexer_id]
            except indexer_error:
                s = None

            if not s:
                return self._finish_early()

            # this usually only happens if they have an NFO in their show dir which gave us a Indexer ID that has no
            # proper english version of the show
            try:
                s.seriesname
            except AttributeError:
                sickrage.app.log.warning("Show in {} has no name on {}, probably the wrong language used to search with".format(self.showDir, index_name))
                sickrage.app.alerts.error(_("Unable to add show"),
                                          _("Show in {} has no name on {}, probably the wrong language. Delete .nfo "
                                            "and add manually in the correct language").format(self.showDir, index_name))
                return self._finish_early()

            # if the show has no episodes/seasons
            if not len(s):
                sickrage.app.log.warning(
                    "Show " + str(s['seriesname']) + " is on " + str(IndexerApi(self.indexer).name) + " but contains no season/episode data.")
                sickrage.app.alerts.error(_("Unable to add show"),
                                          _("Show ") + str(s['seriesname']) + _(" is on ") + str(IndexerApi(self.indexer).name) + _(
                                              " but contains no season/episode data."))
                return self._finish_early()
        except Exception as e:
            sickrage.app.log.error("{}: Error while loading information from indexer {}. Error: {}".format(self.indexer_id, index_name, e))

            sickrage.app.alerts.error(
                _("Unable to add show"),
                _("Unable to look up the show in {} on {} using ID {}, not using the NFO. Delete .nfo and try adding "
                  "manually again.").format(self.showDir, index_name, self.indexer_id)
            )

            if sickrage.app.config.use_trakt:
                title = self.showDir.split("/")[-1]

                data = {
                    'shows': [
                        {
                            'title': title,
                            'ids': {IndexerApi(self.indexer).trakt_id: self.indexer_id}
                        }
                    ]
                }

                TraktAPI()["sync/watchlist"].remove(data)

            return self._finish_early()

        # add show to database
        session.add(TVShow(**{'indexer': self.indexer, 'indexer_id': self.indexer_id, 'lang': self.lang, 'location': self.showDir}))
        session.commit()

        show_obj = find_show(self.indexer_id, session=session)

        try:
            show_obj.load_from_indexer()

            # set up initial values
            show_obj.subtitles = self.subtitles or sickrage.app.config.subtitles_default
            show_obj.sub_use_sr_metadata = self.sub_use_sr_metadata
            show_obj.quality = self.quality or sickrage.app.config.quality_default
            show_obj.flatten_folders = self.flatten_folders or sickrage.app.config.flatten_folders_default
            show_obj.anime = self.anime or sickrage.app.config.anime_default
            show_obj.scene = self.scene or sickrage.app.config.scene_default
            show_obj.skip_downloaded = self.skip_downloaded or sickrage.app.config.skip_downloaded_default
            show_obj.paused = self.paused or False

            # set up default new/missing episode status
            sickrage.app.log.info("Setting all current episodes to the specified default status: " + str(self.default_status))

            show_obj.default_ep_status = self.default_status

            # save to database
            session.commit()

            if show_obj.anime:
                if self.blacklist:
                    show_obj.release_groups.set_black_keywords(self.blacklist)
                if self.whitelist:
                    show_obj.release_groups.set_white_keywords(self.whitelist)
        except indexer_exception as e:
            sickrage.app.log.warning(_("Unable to add show due to an error with ") + IndexerApi(self.indexer).name + ": {}".format(e))
            if show_obj:
                sickrage.app.alerts.error(_("Unable to add ") + str(show_obj.name) + _(" due to an error with ") + IndexerApi(self.indexer).name + "")
            else:
                sickrage.app.alerts.error(_("Unable to add show due to an error with ") + IndexerApi(self.indexer).name + "")
            return self._finish_early()

        except MultipleShowObjectsException:
            sickrage.app.log.warning(_("The show in ") + self.showDir + _(" is already in your show list, skipping"))
            sickrage.app.alerts.error(_('Show skipped'),
                                      _("The show in ") + self.showDir + _(" is already in your show list"))
            return self._finish_early()

        except Exception as e:
            sickrage.app.log.error(_("Error trying to add show: {}").format(e))
            sickrage.app.log.debug(traceback.format_exc())
            raise self._finish_early()

        # add show to name cache
        sickrage.app.name_cache.build(show_obj)

        try:
            sickrage.app.log.debug(_("Attempting to retrieve show info from IMDb"))
            show_obj.load_imdb_info()
        except Exception as e:
            sickrage.app.log.debug(_("Error loading IMDb info: {}").format(e))
            sickrage.app.log.debug(traceback.format_exc())

        try:
            show_obj.load_episodes_from_indexer()
        except Exception as e:
            sickrage.app.log.debug(_("Error with ") + IndexerApi(show_obj.indexer).name + _(", not creating episode list: {}").format(e))
            sickrage.app.log.debug(traceback.format_exc())

        try:
            show_obj.load_episodes_from_dir()
        except Exception as e:
            sickrage.app.log.debug("Error searching dir for episodes: {}".format(e))
            sickrage.app.log.debug(traceback.format_exc())

        # save to database
        session.commit()

        sickrage.app.io_loop.add_callback(show_obj.write_metadata, force=True)
        sickrage.app.io_loop.add_callback(show_obj.populate_cache)

        if sickrage.app.config.use_trakt:
            # if there are specific episodes that need to be added by trakt
            sickrage.app.trakt_searcher.manage_new_show(show_obj)

            # add show to trakt.tv library
            if sickrage.app.config.trakt_sync:
                sickrage.app.trakt_searcher.add_show_to_trakt_library(show_obj)

            if sickrage.app.config.trakt_sync_watchlist:
                sickrage.app.log.info("update watchlist")
                sickrage.app.notifier_providers['trakt'].update_watchlist(show_obj)

        # Load XEM data to DB for show
        xem_refresh(show_obj.indexer_id, show_obj.indexer, force=True, session=session)

        # check if show has XEM mapping so we can determine if searches should go by scene numbering or indexer
        # numbering.
        if not self.scene and get_xem_numbering_for_show(show_obj.indexer_id, show_obj.indexer):
            show_obj.scene = 1
            session.commit()

        # if they set default ep status to WANTED then run the backlog to search for episodes
        if show_obj.default_ep_status == WANTED:
            sickrage.app.log.info(_("Launching backlog for this show since it has episodes that are WANTED"))
            sickrage.app.io_loop.add_callback(sickrage.app.backlog_searcher.search_backlog, show_obj.indexer_id, session=session)

        show_obj.default_ep_status = self.default_status_after
        session.commit()

        sickrage.app.quicksearch_cache.add_show(show_obj.indexer_id)

        sickrage.app.log.info("Finished adding show {} in {}s from show dir: {}".format(self.show_name, round(time.time() - start_time, 2), self.showDir))

    def _finish_early(self):
        try:
            sickrage.app.show_queue.remove_show(self.indexer_id)
        except CantRemoveShowException:
            pass


class QueueItemRefresh(ShowQueueItem):
    def __init__(self, indexer_id=None, force=False):
        super(QueueItemRefresh, self).__init__(indexer_id, ShowQueueActions.REFRESH)

        # force refresh certain items
        self.force = force

    @MainDB.with_session
    def run(self, session=None):
        show_obj = find_show(self.indexer_id, session=session)

        start_time = time.time()

        sickrage.app.log.info("Performing refresh for show: {}".format(show_obj.name))

        show_obj.refresh_dir()

        show_obj.write_metadata(force=self.force)
        show_obj.populate_cache(force=self.force)

        # Load XEM data to DB for show
        # xem_refresh(show.indexer_id, show.indexer)

        show_obj.last_refresh = datetime.date.today().toordinal()

        sickrage.app.log.info("Finished refresh in {}s for show: {}".format(round(time.time() - start_time, 2), show_obj.name))


class QueueItemRename(ShowQueueItem):
    def __init__(self, indexer_id=None):
        super(QueueItemRename, self).__init__(indexer_id, ShowQueueActions.RENAME)

    @MainDB.with_session
    def run(self, session=None):
        show_obj = find_show(self.indexer_id, session=session)

        sickrage.app.log.info("Performing renames for show: {}".format(show_obj.name))

        if not os.path.isdir(show_obj.location):
            sickrage.app.log.warning(
                "Can't perform rename on " + show_obj.name + " when the show dir is missing.")
            return

        ep_obj_rename_list = []

        ep_obj_list = show_obj.get_all_episodes(has_location=True)
        for cur_ep_obj in ep_obj_list:
            # Only want to rename if we have a location
            if cur_ep_obj.location:
                if cur_ep_obj.related_episodes:
                    # do we have one of multi-episodes in the rename list already
                    have_already = False
                    for cur_related_ep in cur_ep_obj.related_episodes + [cur_ep_obj]:
                        if cur_related_ep in ep_obj_rename_list:
                            have_already = True
                            break
                    if not have_already:
                        ep_obj_rename_list.append(cur_ep_obj)

                else:
                    ep_obj_rename_list.append(cur_ep_obj)

        for cur_ep_obj in ep_obj_rename_list:
            cur_ep_obj.rename()

        sickrage.app.log.info("Finished renames for show: {}".format(show_obj.name))


class QueueItemSubtitle(ShowQueueItem):
    def __init__(self, indexer_id=None):
        super(QueueItemSubtitle, self).__init__(indexer_id, ShowQueueActions.SUBTITLE)

    @MainDB.with_session
    def run(self, session=None):
        show_obj = find_show(self.indexer_id, session=session)

        sickrage.app.log.info("Started downloading subtitles for show: {}".format(show_obj.name))

        show_obj.download_subtitles()

        sickrage.app.log.info("Finished downloading subtitles for show: {}".format(show_obj.name))


class QueueItemUpdate(ShowQueueItem):
    def __init__(self, indexer_id=None, indexer_update_only=False, action_id=ShowQueueActions.UPDATE):
        super(QueueItemUpdate, self).__init__(indexer_id, action_id)
        self.indexer_update_only = indexer_update_only
        self.force = False

    @MainDB.with_session
    def run(self, session=None):
        show_obj = find_show(self.indexer_id, session=session)

        start_time = time.time()

        sickrage.app.log.info("Performing updates for show: {}".format(show_obj.name))

        try:
            sickrage.app.log.debug("Retrieving show info from " + IndexerApi(show_obj.indexer).name + "")
            show_obj.load_from_indexer(cache=False)
        except indexer_error as e:
            sickrage.app.log.warning("Unable to contact " + IndexerApi(show_obj.indexer).name + ", aborting: {}".format(e))
            return
        except indexer_attributenotfound as e:
            sickrage.app.log.warning("Data retrieved from " + IndexerApi(show_obj.indexer).name + " was incomplete, aborting: {}".format(e))
            return

        try:
            if not self.indexer_update_only:
                sickrage.app.log.debug("Attempting to retrieve show info from IMDb")
                show_obj.load_imdb_info()
        except Exception as e:
            sickrage.app.log.warning("Error loading IMDb info for {}: {}".format(IndexerApi(show_obj.indexer).name, e))

        # get episode list from DB
        db_ep_list = {dbData.season: {dbData.episode: True} for dbData in show_obj.episodes}
        indexer_ep_list = None

        # get episode list from TVDB
        try:
            indexer_ep_list = show_obj.load_episodes_from_indexer()
        except indexer_exception as e:
            sickrage.app.log.warning("Unable to get info from " + IndexerApi(show_obj.indexer).name + ", the show info will not be refreshed: {}".format(e))

        if not indexer_ep_list:
            sickrage.app.log.warning("No data returned from " + IndexerApi(show_obj.indexer).name + ", unable to update this show")
        else:
            # for each ep we found on indexer delete it from the DB list
            for curSeason in indexer_ep_list:
                for curEpisode in indexer_ep_list[curSeason]:
                    if curSeason in db_ep_list and curEpisode in db_ep_list[curSeason]:
                        del db_ep_list[curSeason][curEpisode]

            # remaining episodes in the DB list are not on the indexer, just delete them from the DB
            for curSeason in db_ep_list:
                for curEpisode in db_ep_list[curSeason]:
                    sickrage.app.log.info("Permanently deleting episode " + str(curSeason) + "x" + str(curEpisode) + " from the database")
                    try:
                        show_obj.get_episode(curSeason, curEpisode).delete_episode()
                    except EpisodeDeletedException:
                        continue

        sickrage.app.quicksearch_cache.update_show(show_obj.indexer_id)

        sickrage.app.log.info("Finished updates in {}s for show: {}".format(round(time.time() - start_time, 2), show_obj.name))

        session.commit()

        # refresh show
        if not self.indexer_update_only:
            sickrage.app.show_queue.refresh_show(show_obj.indexer_id, self.force)


class QueueItemForceUpdate(QueueItemUpdate):
    def __init__(self, indexer_id=None, indexer_update_only=False):
        super(QueueItemForceUpdate, self).__init__(indexer_id, indexer_update_only, ShowQueueActions.FORCEUPDATE)
        self.indexer_update_only = indexer_update_only
        self.force = True


class QueueItemRemove(ShowQueueItem):
    def __init__(self, indexer_id=None, full=False):
        super(QueueItemRemove, self).__init__(indexer_id, ShowQueueActions.REMOVE)

        # lets make sure this happens before any other high priority actions
        self.priority = SRQueuePriorities.EXTREME
        self.full = full

    @property
    def is_loading(self):
        """
        Returns false cause we are removing the show.
        """
        return False

    @MainDB.with_session
    def run(self, session=None):
        show_obj = find_show(self.indexer_id, session=session)

        sickrage.app.log.info("Removing show: {}".format(show_obj.name))

        sickrage.app.quicksearch_cache.del_show(show_obj.indexer_id)

        show_obj.delete_show(full=self.full)

        if sickrage.app.config.use_trakt:
            try:
                sickrage.app.trakt_searcher.remove_show_from_trakt_library(show_obj)
            except Exception as e:
                sickrage.app.log.warning(
                    "Unable to delete show from Trakt: %s. Error: %s" % (show_obj.name, e))

        sickrage.app.log.info("Finished removing show: {}".format(show_obj.name))
