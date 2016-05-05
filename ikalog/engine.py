#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  IkaLog
#  ======
#  Copyright (C) 2015 Takeshi HASEGAWA
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from __future__ import print_function

import copy
import cv2
import pprint
import sys
import time
import traceback

from ikalog.utils import *
from . import scenes

# The IkaLog core engine.
#


class IkaEngine:

    # Profiling

    def _profile_dump_scenes(self):
        for scene in self.scenes:
            print('%4.3fs %s' % (scene._prof_time_took, scene))

    def _profile_dump(self):
        self._profile_dump_scenes()

    def enable_profile(self):
        self._enable_profile = True

    def disble_profile(self):
        self._enable_profile = False

    # Exception Logging

    def _exception_log_init(self, context):
        context['engine']['exceptions_log'] = {}

    def _exception_log_dump(self, context):
        if not 'exceptions_log' in context['engine']:
            self._exception_log_init(context)

        if len(context['engine']['exceptions_log']) > 0:
            pprint.pprint(context['engine']['exceptions_log'])

    def _exception_log_append(self, context, name, text):
        if not 'exceptions_log' in context['engine']:
            self._exception_log_init(context)

        d = context['engine']['exceptions_log']

        count = d.get(name, {'count': 0})['count']
        d[name] = {
            'count': count + 1,
            'text': text,
        }

    #

    def on_game_individual_result(self, context):
        self.session_close_wdt = context['engine']['msec'] + (20 * 1000)

    def on_result_gears(self, context):
        if self.session_close_wdt is not None:
            self.session_close_wdt = context['engine']['msec'] + (1 * 1000)

    def on_game_lost_sync(self, context):
        self.session_abort()

    def dprint(self, text):
        print(text, file=sys.stderr)

    def call_plugins(self, event_name, params=None, debug=False):
        if debug:
            self.dprint('call plug-in hook (%s):' % event_name)

        for op in self.output_plugins:
            if hasattr(op, event_name):
                if debug:
                    self.dprint('Call  %s' % op.__class__.__name__)
                try:
                    if params is None:
                        getattr(op, event_name)(self.context)
                    else:
                        getattr(op, event_name)(self.context, params)
                except:
                    self.dprint('%s.%s() raised a exception >>>>' %
                                (op.__class__.__name__, event_name))
                    self.dprint(traceback.format_exc())
                    self.dprint('<<<<<')
            elif hasattr(op, 'onUncatchedEvent'):
                if debug:
                    self.dprint(
                        'call plug-in hook (UncatchedEvent, %s):' % event_name)
                try:
                    getattr(op, 'onUncatchedEvent')(event_name, self.context)
                except:
                    self.dprint('%s.%s() raised a exception >>>>' %
                                (op.__class__.__name__, event_name))
                    self.dprint(traceback.format_exc())
                    self.dprint('<<<<<')

    def call_plugins_later(self, event_name, params=None, debug=False):
        self._event_queue.append((event_name, params))

    def read_next_frame(self, skip_frames=0):
        for i in range(skip_frames):
            frame = self.capture.read_frame()
        frame = self.capture.read_frame()

        while frame is None:
            self.call_plugins('on_frame_read_failed')
            if self._stop:
                return None, None
            cv2.waitKey(1000)
            frame = self.capture.read_frame()

        t = self.capture.get_current_timestamp()
        self.context['engine']['msec'] = t
        self.context['engine']['frame'] = frame
        self.context['engine']['preview'] = copy.deepcopy(frame)

        self.call_plugins('on_debug_read_next_frame')

        return frame, t

    def stop(self):
        self.call_plugins('on_stop')
        self._stop = True

    def reset(self):
        # Initalize the context
        self.context['game'] = {
            'map': None,
            'rule': None,
            'won': None,
            'players': None,

            'kills': 0,
            'dead': False,
            'death_reasons': {},

            'inkling_state': [None, None],
            'livesTrack': [],
            'towerTrack': [],

            # Float values of start and end times scince the epoch in second.
            # They are used with IkaUtils.GetTime.
            'start_time': None,
            'end_time': None,
            # Int values of start and end offset times in millisecond.
            # They are used with context['engine']['msec']
            'start_offset_msec': None,
            'end_offset_msec': None,
        }
        self.call_plugins('on_game_reset')
        self._exception_log_init(self.context)

    def create_context(self):
        self.context = {
            'engine': {
                'epoch_time': None,
                'frame': None,
                'service': {
                    'call_plugins': self.call_plugins,
                    'call_plugins_later': self.call_plugins,
                    # For backward compatibility
                    'callPlugins': self.call_plugins,
                },
                'exceptions_log': {
                },
            },
            'scenes': {
            },
            'config': {
            },
            'lobby': {
            }
        }
        self.reset()
        self.session_close_wdt = None

    def session_close(self):
        self.session_close_wdt = None

        if not self.context['game']['end_time']:
            # end_time should be initialized in GameFinish.
            # This is a fallback in case GameFinish was skipped.
            self.context['game']['end_time'] = IkaUtils.getTime(self.context)
            self.context['game']['end_offset_msec'] = self.context['engine']['msec']


        self.call_plugins('on_game_session_end')
        self.reset()

    def session_abort(self):
        self.session_close_wdt = None

        if not self.context['game']['end_time']:
            # end_time should be initialized in GameFinish or session_close.
            # This is a fallback in case they were skipped.
            self.context['game']['end_time'] = IkaUtils.getTime(self.context)
            self.context['game']['end_offset_msec'] = self.context['engine'].get('msec', None)

        self.call_plugins('on_game_session_abort')
        self.reset()

    def process_scene(self, scene):
        context = self.context

        try:
            scene.new_frame(context)
            scene.match(context)
        except:
            if self._abort_at_scene_exception:
                raise

            scene_name = scene.__class__.__name__
            desc = traceback.format_exc()

            self.dprint('%s raised a exception >>>>' % scene_name)
            self.dprint(desc)
            self.dprint('<<<<<')

            self._exception_log_append(context, scene_name, desc)

    def find_scene_object(self, scene_class_name):
        for scene in self.scenes:
            if scene.__class__.__name__ == scene_class_name:
                return scene
        return None

    def process_frame(self):
        context = self.context

        frame, t = self.read_next_frame()

        if frame is None:
            return False

        context['engine']['inGame'] = self.find_scene_object(
            'GameTimerIcon').match(context)

        self.call_plugins('on_frame_read')

        for scene in self.scenes:
            self.process_scene(scene)

        if self.session_close_wdt is not None:
            if self.session_close_wdt < context['engine']['msec']:
                self.dprint('Watchdog fired. Closing current session')
                self.session_close()

        key = None

        self.call_plugins('on_draw_preview')
        self.call_plugins('on_show_preview')

        # FixMe: Since on_frame_next and on_key_press has non-standard arguments,
        # self.call_plugins() doesn't work for those.

        for op in self.output_plugins:
            if hasattr(op, "on_frame_next"):
                try:
                    key = op.on_frame_next(context)
                except:
                    pass

        for op in self.output_plugins:
            if hasattr(op, "on_key_press"):
                try:
                    op.on_key_press(context, key)
                except:
                    pass

        while len(self._event_queue) > 0:
            event = self._event_queue.pop(0)
            self.call_plugins(event_name=event[0], params=event[1])

    def _main_loop(self):
        while not self._stop:
            if self._pause:
                time.sleep(0.5)
                continue

            try:
                self.process_frame()
            except EOFError:
                # EOF. Close session if close_session_at_eof is set.
                if self.close_session_at_eof:
                    if self.session_close_wdt is not None:
                        self.dprint('Closing current session at EOF')
                        self.session_close()
                    else:
                        self.session_abort()

                self._stop = True

        cv2.destroyAllWindows()

    def run(self):
        try:
            self._main_loop()
        finally:
            if self._enable_profile:
                self._profile_dump()

            if 1:
                self._exception_log_dump(self.context)

    def set_capture(self, capture):
        self.capture = capture
        self.context['engine']['input_class'] = self.capture.__class__.__name__

    def set_epoch_time(self, epoch_time):
        self.context['engine']['epoch_time'] = epoch_time

    def set_plugins(self, plugins):
        self.output_plugins = [self]
        self.output_plugins.extend(self.scenes)
        self.output_plugins.extend(plugins)

    def pause(self, pause):
        self._pause = pause

    def _initialize_scenes(self):
        self.scenes = [
            scenes.GameTimerIcon(self),
            scenes.GameStart(self),
            scenes.GameGoSign(self),
            scenes.GameKill(self),
            scenes.GameDead(self),
            scenes.GameOutOfBound(self),
            scenes.GameFinish(self),
            scenes.GameSpecialGauge(self),
            scenes.GameSpecialWeapon(self),

            scenes.GameRankedBattleEvents(self),
            scenes.PaintScoreTracker(self),
            scenes.ObjectiveTracker(self),
            scenes.SplatzoneTracker(self),
            scenes.InklingsTracker(self),

            scenes.ResultJudge(self),
            scenes.ResultDetail(self),
            scenes.ResultUdemae(self),
            scenes.ResultGears(self),
            scenes.ResultFesta(self),

            scenes.Lobby(self),
#            scenes.Downie(self),

            scenes.Blank(self),
        ]

    def __init__(self, enable_profile=False, abort_at_scene_exception=False):
        self._initialize_scenes()

        self.output_plugins = [self]
        self.last_capture = time.time() - 100

        self._stop = False
        self._pause = True
        self._event_queue = []

        self.close_session_at_eof = False
        self._enable_profile = enable_profile
        self._abort_at_scene_exception = abort_at_scene_exception

        self.create_context()
