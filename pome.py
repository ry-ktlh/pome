#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pome — ポメラ風コマンドラインエディタ

King Jim ポメラ DM250 の内蔵エディタの外観・操作性を参考にした、
curses ベースの軽量日本語テキストエディタ。
Debian bullseye の python3 標準ライブラリのみで動作する。

使い方:
    pome [ファイル名]

ファイル名を省略すると、前回開いていたファイルを自動的に開く。
"""

import curses
import datetime
import hashlib
import json
import locale
import os
import stat
import tempfile
import subprocess
import sys
import time
import unicodedata

VERSION = "1.1.2"
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or
                          os.path.expanduser("~/.config"), "pome")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
HISTORY_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or
                           os.path.expanduser("~/.local/state"), "pome", "history")

DEFAULT_CONFIG = {
    "ambiguous_wide": True,   # 曖昧幅文字(※や○など)を全角として扱う
    "autosave": True,         # アイドル時に自動保存
    "autosave_idle_sec": 3.0,
    "tab_width": 4,
    "undo_limit": 200,
    "git_timeout_sec": 180,
    "last_file": "",
}

# 行頭に来てはいけない文字（簡易禁則）
KINSOKU_HEAD = set(
    "、。，．・：；？！ヽヾゝゞ々ー～…‥"
    "）〕］｝〉》」』】〙〗"
    "ぁぃぅぇぉっゃゅょゎゕゖ"
    "ァィゥェォッャュョヮヵヶ"
    ",.!?;:)]}"
)

WEEKDAYS_JA = "月火水木金土日"

CTRL_MAP = {
    "\x11": "quit",         # ^Q
    "\x13": "file_save",    # ^S
    "\x0f": "file_open",    # ^O
    "\x0e": "file_new",     # ^N
    "\x06": "search",       # ^F
    "\x0c": "search_next",  # ^L
    "\x05": "search_prev",  # ^E
    "\x12": "replace",      # ^R
    "\x07": "jump",         # ^G
    "\x1a": "undo",         # ^Z
    "\x19": "redo",         # ^Y
    "\x18": "cut",          # ^X
    "\x03": "copy",         # ^C
    "\x16": "paste",        # ^V
    "\x01": "select_all",   # ^A
    "\x00": "toggle_mark",  # ^Space
    "\x17": "count",        # ^W
    "\x14": "insert_date",  # ^T
}

MENUS = [
    ("ファイル", [
        ("新規作成", "^N", "file_new"),
        ("開く...", "^O", "file_open"),
        ("上書き保存", "^S", "file_save"),
        ("名前を付けて保存...", "", "file_saveas"),
        ("保存履歴から復元...", "", "file_history"),
        None,
        ("終了", "^Q", "quit"),
    ]),
    ("編集", [
        ("元に戻す", "^Z", "undo"),
        ("やり直し", "^Y", "redo"),
        None,
        ("切り取り", "^X", "cut"),
        ("コピー", "^C", "copy"),
        ("貼り付け", "^V", "paste"),
        None,
        ("すべて選択", "^A", "select_all"),
        ("選択開始/解除", "^Spc", "toggle_mark"),
        None,
        ("日時を挿入", "^T", "insert_date"),
    ]),
    ("検索", [
        ("検索...", "^F", "search"),
        ("次を検索", "^L", "search_next"),
        ("前を検索", "^E", "search_prev"),
        ("置換...", "^R", "replace"),
        None,
        ("指定行へジャンプ...", "^G", "jump"),
        ("文頭へ", "", "goto_top"),
        ("文末へ", "", "goto_bottom"),
    ]),
    ("ツール", [
        ("文字数カウント", "^W", "count"),
        None,
        ("Git プル（受信）", "", "git_pull"),
        ("Git プッシュ（送信）", "", "git_push"),
        ("Git 状態表示", "", "git_status"),
        None,
        ("自動保存 ON/OFF", "", "toggle_autosave"),
        ("曖昧幅文字 全角/半角", "", "toggle_ambw"),
    ]),
    ("ヘルプ", [
        ("キー操作一覧", "", "help"),
        ("バージョン情報", "", "about"),
    ]),
]

HELP_LINES = [
    "Esc          メニューを開く / 選択を解除",
    "",
    "--- ファイル ---",
    "Ctrl+S       上書き保存",
    "Ctrl+O       ファイルを開く",
    "Ctrl+N       新規作成",
    "Ctrl+Q       終了",
    "",
    "--- 編集 ---",
    "Ctrl+Z / Y   元に戻す / やり直し",
    "Ctrl+Space   選択開始（カーソル移動で範囲を広げる）",
    "Ctrl+X/C/V   切り取り / コピー / 貼り付け",
    "Ctrl+A       すべて選択",
    "Ctrl+T       日時を挿入",
    "",
    "--- 検索・移動 ---",
    "Ctrl+F       検索",
    "Ctrl+L / E   次を検索 / 前を検索",
    "Ctrl+R       置換",
    "Ctrl+G       指定行へジャンプ",
    "Home / End   行頭 / 行末",
    "PgUp / PgDn  ページ移動",
    "",
    "--- その他 ---",
    "Ctrl+W       文字数カウント",
    "Git 連携はメニューの「ツール」から",
    "保存済みの内容を戻す: ファイル → 保存履歴から復元",
    "無題の原稿は最初に Ctrl+S で名前を付けると自動保存されます",
]


# ---------------------------------------------------------------- 文字幅

def make_char_width(ambiguous_wide, tab_width):
    """文字→表示幅 の関数を作る（結果はキャッシュされる）"""
    cache = {}

    def cw(ch):
        w = cache.get(ch)
        if w is None:
            if ch == "\t":
                w = tab_width
            elif unicodedata.combining(ch):
                w = 0
            else:
                ea = unicodedata.east_asian_width(ch)
                if ea in ("W", "F"):
                    w = 2
                elif ea == "A":
                    w = 2 if ambiguous_wide else 1
                else:
                    w = 1
            cache[ch] = w
        return w

    return cw


def wrap_segments(s, width, cw):
    """文字列 s を表示幅 width で折り返し、[(開始, 終了), ...] を返す"""
    if width < 2:
        width = 2
    if not s:
        return [(0, 0)]
    segs = []
    start = 0
    w = 0
    i = 0
    n = len(s)
    while i < n:
        c = cw(s[i])
        if w + c > width and i > start:
            brk = i
            # 簡易禁則: 行頭に句読点等が来るなら直前の文字ごと追い出す
            if s[i] in KINSOKU_HEAD and brk - 1 > start:
                brk -= 1
            segs.append((start, brk))
            start = brk
            i = brk
            w = 0
            continue
        w += c
        i += 1
    segs.append((start, n))
    return segs


def text_dw(s, cw):
    return sum(cw(ch) for ch in s)


def truncate_dw(s, maxw, cw):
    """表示幅 maxw に収まるよう末尾を切り、(文字列, 実幅) を返す"""
    w = 0
    out = []
    for ch in s:
        c = cw(ch)
        if w + c > maxw:
            break
        out.append(ch)
        w += c
    return "".join(out), w


def pad_dw(s, width, cw):
    t, w = truncate_dw(s, width, cw)
    return t + " " * max(0, width - w)


# ---------------------------------------------------------------- 本体

class Editor:
    def __init__(self, path):
        self.config = dict(DEFAULT_CONFIG)
        self.load_config()
        self.cw = make_char_width(self.config["ambiguous_wide"],
                                  self.config["tab_width"])

        self.lines = [""]
        self.path = None
        self._saved_text = ""
        self._disk_data = None
        self.encoding = "utf-8"
        self.crlf = False
        self.dirty = False
        self._autosave_failed = False
        self.version = 0

        self.cy = 0
        self.cx = 0
        self.want_x = None
        self.top = (0, 0)          # 画面最上段の (行, 折返しセグメント)
        self.anchor = None          # 選択開始位置 (行, 桁) または None
        self.clip = ""

        self.undo_stack = []
        self.redo_stack = []
        self.last_kind = None
        self.last_edit_time = 0.0
        self.last_change_time = 0.0

        self.search_q = ""
        self.replace_q = ""
        self.flash = None           # (メッセージ, 期限時刻)
        self.quitting = False

        self.scr = None
        self._size = (0, 0)
        self._seg_cache = {}
        self._seg_width = -1
        self._count_ver = -1
        self._count_val = 0

        if path:
            self.load_file(path)
        else:
            last = self.config.get("last_file", "")
            if last and os.path.isfile(last):
                self.load_file(last)

    # ------------------------------------------------------------ 設定

    def load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k in DEFAULT_CONFIG:
                if k in data:
                    self.config[k] = data[k]
        except Exception:
            pass

    def save_config(self):
        """設定ファイルは一時ファイル経由で置き換え、書込中断による破損を減らす。"""
        temporary = None
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".pome-config-", dir=CONFIG_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, CONFIG_PATH)
            temporary = None
        except OSError:
            # 設定の保存失敗で本文の保存結果を失敗扱いにしない。
            pass
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    # ------------------------------------------------------------ 入出力

    def load_file(self, path):
        path = os.path.realpath(path)
        raw = None
        text = ""
        encoding = "utf-8"
        if os.path.exists(path):
            with open(path, "rb") as f:
                raw = f.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("cp932")
                    encoding = "cp932"
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", errors="replace")
        self.crlf = "\r\n" in text
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.lines = text.split("\n")
        self._saved_text = text
        self._disk_data = raw
        self.path = path
        self.encoding = encoding
        self.dirty = False
        self._autosave_failed = False
        self.version += 1
        self.cy = self.cx = 0
        self.want_x = None
        self.top = (0, 0)
        self.anchor = None
        self.undo_stack = []
        self.redo_stack = []
        self.last_kind = None
        self.config["last_file"] = path
        self.save_config()

    def save_file(self, path=None):
        """保存する。成功したら True"""
        if path is None:
            path = self.path
        if path is None:
            return self.act_file_saveas()
        path = os.path.realpath(path)
        text = "\n".join(self.lines)
        encoded_text = text.replace("\n", "\r\n") if self.crlf else text
        encoding = self.encoding
        try:
            data = encoded_text.encode(encoding)
        except UnicodeEncodeError:
            data = encoded_text.encode("utf-8")
            encoding = "utf-8"
        tmp = None
        try:
            try:
                with open(path, "rb") as f:
                    previous = f.read()
                    mode = stat.S_IMODE(os.fstat(f.fileno()).st_mode)
            except FileNotFoundError:
                previous, mode = None, None
            if path == self.path and previous != self._disk_data:
                self.msgbox("保存を中止しました", [
                    "原稿が別のプログラムで変更または削除されています。",
                    "名前を付けて保存で、現在の内容を別ファイルに保存してください。"])
                return False
            if previous != data:
                # Never replace a manuscript unless its old bytes are recoverable.
                if previous is not None:
                    self.keep_history(path, previous)
                fd, tmp = tempfile.mkstemp(prefix=".pome-", dir=os.path.dirname(path))
                with os.fdopen(fd, "wb") as f:
                    if mode is not None:
                        os.fchmod(f.fileno(), mode)
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
        except OSError as e:
            self.msgbox("保存エラー", [str(e), "元の原稿は上書きしていません。"])
            return False
        finally:
            if tmp is not None and os.path.exists(tmp):
                os.unlink(tmp)
        if encoding != self.encoding:
            self.flash_msg("cp932にない文字があるためUTF-8で保存しました", 4)
        self.encoding = encoding
        self.path = path
        self._saved_text = text
        self._disk_data = data
        self.dirty = False
        self._autosave_failed = False
        self.config["last_file"] = self.path
        self.save_config()
        return True

    @staticmethod
    def history_directory(path):
        key = hashlib.sha256(os.fsencode(os.path.realpath(path))).hexdigest()
        return os.path.join(HISTORY_DIR, key)

    @classmethod
    def keep_history(cls, path, data):
        directory = cls.history_directory(path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f-")
        fd, backup = tempfile.mkstemp(prefix=stamp, suffix=".txt", dir=directory)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            os.unlink(backup)
            raise
        return backup

    def history_files(self):
        if self.path is None:
            return []
        directory = self.history_directory(self.path)
        try:
            return [os.path.join(directory, name)
                    for name in sorted(os.listdir(directory), reverse=True)
                    if name.endswith(".txt")]
        except FileNotFoundError:
            return []

    def act_file_history(self):
        try:
            files = self.history_files()
            if not files:
                self.msgbox("保存履歴", ["この原稿の保存履歴はまだありません。"])
                return
            self.msgbox("保存履歴（新しい順）", [
                "%d. %s" % (i + 1, os.path.basename(path))
                for i, path in enumerate(files)])
            number = self.prompt("復元する番号（Escで中止）: ")
            if number is None:
                return
            try:
                index = int(number) - 1
            except ValueError:
                index = -1
            if not 0 <= index < len(files):
                self.flash_msg("一覧にある番号を入力してください")
                return
            with open(files[index], "rb") as f:
                raw = f.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp932")
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            self.msgbox("復元内容のプレビュー", text.split("\n"))
            if self.choice("この内容を復元しますか？ (y)復元 (Esc)中止", ("y",)) != "y":
                return
            self.checkpoint("block")
            self.lines = text.split("\n")
            self.cy = self.cx = 0
            self.top = (0, 0)
            self.anchor = None
            self.mark_changed()
            self.last_kind = None
            self.flash_msg("復元しました（Ctrl+Zで取り消し、Ctrl+Sで保存）", 4)
        except (OSError, UnicodeError) as e:
            self.msgbox("履歴の読み込みエラー", [str(e)])

    def emergency_save(self):
        """異常終了時の未保存データを、上書きしない固有のファイルへ退避。"""
        if not self.dirty:
            return None
        base = self.path or os.path.expanduser("~/無題.txt")
        rescue = None
        try:
            # Filename component has a byte limit (often 255 on ext4).
            # Hash the original path instead of embedding a potentially
            # very long UTF-8 manuscript filename in the rescue basename.
            identifier = hashlib.sha256(os.fsencode(base)).hexdigest()[:12]
            fd, rescue = tempfile.mkstemp(
                prefix=".pome-rescue-" + identifier + "-",
                suffix=".txt", dir=os.path.dirname(base))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(self.lines))
                f.flush()
                os.fsync(f.fileno())
            return rescue
        except OSError:
            if rescue:
                try:
                    os.unlink(rescue)
                except OSError:
                    pass
            return None

    # ------------------------------------------------------------ 折返し

    def segs(self, s):
        c = self._seg_cache.get(s)
        if c is None:
            if len(self._seg_cache) > 5000:
                self._seg_cache.clear()
            c = wrap_segments(s, self.text_width(), self.cw)
            self._seg_cache[s] = c
        return c

    def text_width(self):
        h, w = self.scr.getmaxyx()
        return max(2, w - 1)

    def reset_wrap(self):
        self._seg_cache.clear()

    def cursor_seg(self):
        """カーソルのある折返しセグメント番号と (開始, 終了) を返す"""
        segs = self.segs(self.lines[self.cy])
        last = len(segs) - 1
        for k, (a, b) in enumerate(segs):
            if self.cx < b or (self.cx == b and k == last):
                return k, a, b
        return last, segs[last][0], segs[last][1]

    def next_row(self, pos):
        li, k = pos
        if k + 1 < len(self.segs(self.lines[li])):
            return (li, k + 1)
        if li + 1 < len(self.lines):
            return (li + 1, 0)
        return None

    def prev_row(self, pos):
        li, k = pos
        if k > 0:
            return (li, k - 1)
        if li > 0:
            return (li - 1, len(self.segs(self.lines[li - 1])) - 1)
        return None

    def col_at(self, li, a, cx):
        s = self.lines[li]
        return text_dw(s[a:cx], self.cw)

    def index_at_col(self, li, a, b, want, is_last_seg):
        s = self.lines[li]
        x = 0
        i = a
        while i < b:
            w = self.cw(s[i])
            if x + w > want:
                return i
            x += w
            i += 1
        if not is_last_seg and i > a:
            return i - 1
        return i

    def ensure_visible(self, text_h):
        # top の正当性を確認
        tl, tk = self.top
        if tl >= len(self.lines):
            tl, tk = len(self.lines) - 1, 0
        nseg = len(self.segs(self.lines[tl]))
        if tk >= nseg:
            tk = nseg - 1
        self.top = (tl, tk)

        k, _, _ = self.cursor_seg()
        cur = (self.cy, k)
        if cur < self.top:
            self.top = cur
            return
        pos = self.top
        for _ in range(text_h):
            if pos == cur:
                return
            pos = self.next_row(pos)
            if pos is None:
                break
        pos = cur
        for _ in range(text_h - 1):
            p = self.prev_row(pos)
            if p is None:
                break
            pos = p
        self.top = pos

    # ------------------------------------------------------------ 選択

    def sel_range(self):
        """選択範囲を ((開始行,桁),(終了行,桁)) で返す。無ければ None"""
        if self.anchor is None:
            return None
        a = self.anchor
        b = (self.cy, self.cx)
        if a == b:
            return None
        return (a, b) if a < b else (b, a)

    def selection_text(self):
        r = self.sel_range()
        if r is None:
            return ""
        (sl, sc), (el, ec) = r
        if sl == el:
            return self.lines[sl][sc:ec]
        parts = [self.lines[sl][sc:]]
        parts.extend(self.lines[sl + 1:el])
        parts.append(self.lines[el][:ec])
        return "\n".join(parts)

    def delete_selection(self):
        r = self.sel_range()
        if r is None:
            self.anchor = None
            return
        (sl, sc), (el, ec) = r
        if sl == el:
            self.lines[sl] = self.lines[sl][:sc] + self.lines[sl][ec:]
        else:
            self.lines[sl] = self.lines[sl][:sc] + self.lines[el][ec:]
            del self.lines[sl + 1:el + 1]
        self.cy, self.cx = sl, sc
        self.anchor = None
        self.mark_changed()

    # ------------------------------------------------------------ 編集

    def mark_changed(self):
        self.dirty = True
        self.version += 1
        self.want_x = None
        self.last_change_time = time.time()

    def snapshot(self):
        return (self.lines[:], self.cy, self.cx)

    def restore(self, snap):
        self.lines, self.cy, self.cx = snap[0][:], snap[1], snap[2]
        self.dirty = "\n".join(self.lines) != self._saved_text
        self.last_change_time = time.time()
        self.cy = min(self.cy, len(self.lines) - 1)
        self.cx = min(self.cx, len(self.lines[self.cy]))
        self.anchor = None
        self.version += 1
        self.want_x = None

    def checkpoint(self, kind):
        now = time.time()
        if (kind != self.last_kind or kind == "block"
                or now - self.last_edit_time > 1.5):
            self.undo_stack.append(self.snapshot())
            if len(self.undo_stack) > self.config["undo_limit"]:
                del self.undo_stack[0]
        self.redo_stack.clear()
        self.last_kind = kind
        self.last_edit_time = now

    def insert_text(self, text):
        parts = text.split("\n")
        line = self.lines[self.cy]
        if len(parts) == 1:
            self.lines[self.cy] = line[:self.cx] + text + line[self.cx:]
            self.cx += len(text)
        else:
            tail = line[self.cx:]
            self.lines[self.cy] = line[:self.cx] + parts[0]
            for i, p in enumerate(parts[1:-1], start=1):
                self.lines.insert(self.cy + i, p)
            self.lines.insert(self.cy + len(parts) - 1, parts[-1] + tail)
            self.cy += len(parts) - 1
            self.cx = len(parts[-1])
        self.mark_changed()

    def edit_insert(self, text, kind="type"):
        if self.anchor is not None:
            kind = "block"
        self.checkpoint(kind)
        if self.anchor is not None:
            self.delete_selection()
        self.insert_text(text)

    def backspace(self):
        if self.anchor is not None:
            self.checkpoint("block")
            self.delete_selection()
            return
        if self.cx == 0 and self.cy == 0:
            return
        self.checkpoint("bs")
        if self.cx > 0:
            s = self.lines[self.cy]
            self.lines[self.cy] = s[:self.cx - 1] + s[self.cx:]
            self.cx -= 1
        else:
            self.cx = len(self.lines[self.cy - 1])
            self.lines[self.cy - 1] += self.lines[self.cy]
            del self.lines[self.cy]
            self.cy -= 1
        self.mark_changed()

    def delete_char(self):
        if self.anchor is not None:
            self.checkpoint("block")
            self.delete_selection()
            return
        s = self.lines[self.cy]
        if self.cx >= len(s) and self.cy == len(self.lines) - 1:
            return
        self.checkpoint("del")
        if self.cx < len(s):
            self.lines[self.cy] = s[:self.cx] + s[self.cx + 1:]
        else:
            self.lines[self.cy] += self.lines[self.cy + 1]
            del self.lines[self.cy + 1]
        self.mark_changed()

    # ------------------------------------------------------------ 移動

    def move_left(self):
        if self.cx > 0:
            self.cx -= 1
        elif self.cy > 0:
            self.cy -= 1
            self.cx = len(self.lines[self.cy])
        self.want_x = None

    def move_right(self):
        if self.cx < len(self.lines[self.cy]):
            self.cx += 1
        elif self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = 0
        self.want_x = None

    def move_vert(self, d):
        k, a, b = self.cursor_seg()
        if self.want_x is None:
            self.want_x = self.col_at(self.cy, a, self.cx)
        pos = (self.cy, k)
        target = self.prev_row(pos) if d < 0 else self.next_row(pos)
        if target is None:
            if d < 0:
                self.cy, self.cx = 0, 0
                self.want_x = None
            else:
                self.cy = len(self.lines) - 1
                self.cx = len(self.lines[self.cy])
                self.want_x = None
            return
        li, k2 = target
        segs = self.segs(self.lines[li])
        a2, b2 = segs[k2]
        is_last = (k2 == len(segs) - 1)
        self.cy = li
        self.cx = self.index_at_col(li, a2, b2, self.want_x, is_last)

    def move_page(self, d):
        h, _ = self.scr.getmaxyx()
        for _ in range(max(1, h - 2)):
            self.move_vert(d)

    def move_home(self):
        self.cx = 0
        self.want_x = None

    def move_end(self):
        self.cx = len(self.lines[self.cy])
        self.want_x = None

    # ------------------------------------------------------------ 描画

    def flash_msg(self, s, sec=2.0):
        self.flash = (s, time.time() + sec)

    def flash_now(self, s):
        self.flash_msg(s, 30)
        self.draw()
        self.scr.refresh()

    def total_chars(self):
        if self._count_ver != self.version:
            self._count_val = sum(len(l) for l in self.lines)
            self._count_ver = self.version
        return self._count_val

    def draw(self):
        scr = self.scr
        h, w = scr.getmaxyx()
        if (h, w) != self._size:
            self.reset_wrap()
            self._size = (h, w)
        scr.erase()
        if h < 2 or w < 4:
            return
        text_h = h - 1
        self.ensure_visible(text_h)

        rows = []
        pos = self.top
        for _ in range(text_h):
            rows.append(pos)
            if pos is not None:
                pos = self.next_row(pos)

        sel = self.sel_range()
        for y, p in enumerate(rows):
            if p is None:
                continue
            self.draw_row(y, p, sel, w)

        self.draw_status(h, w)

        # カーソル配置
        k, a, _ = self.cursor_seg()
        cur = (self.cy, k)
        for y, p in enumerate(rows):
            if p == cur:
                x = self.col_at(self.cy, a, self.cx)
                try:
                    scr.move(y, min(x, w - 1))
                except curses.error:
                    pass
                break

    def draw_row(self, y, pos, sel, w):
        li, k = pos
        s = self.lines[li]
        a, b = self.segs(s)[k]
        tabw = self.config["tab_width"]

        parts = [(a, b, curses.A_NORMAL)]
        newline_selected = False
        if sel is not None:
            (sl, sc), (el, ec) = sel
            if sl <= li <= el:
                s0 = a if li > sl else max(a, min(sc, b))
                s1 = b if li < el else max(a, min(ec, b))
                if s1 < s0:
                    s1 = s0
                parts = [(a, s0, curses.A_NORMAL),
                         (s0, s1, curses.A_REVERSE),
                         (s1, b, curses.A_NORMAL)]
                if li < el and b == len(s):
                    newline_selected = True
        x = 0
        for st, en, attr in parts:
            if en <= st:
                continue
            seg = s[st:en].replace("\t", " " * tabw)
            try:
                self.scr.addstr(y, x, seg, attr)
            except curses.error:
                pass
            x += text_dw(s[st:en], self.cw)
        if newline_selected and x < w - 1:
            try:
                self.scr.addstr(y, x, " ", curses.A_REVERSE)
            except curses.error:
                pass

    def draw_status(self, h, w):
        name = os.path.basename(self.path) if self.path else "無題"
        mark = "*" if self.dirty else " "
        msg = ""
        if self.flash:
            if time.time() < self.flash[1]:
                msg = self.flash[0]
            else:
                self.flash = None
        left = " %s%s  %s" % (name, mark, msg)
        right = "%d行 %d字目  計%s字 " % (
            self.cy + 1, self.cx + 1, format(self.total_chars(), ","))
        rtext, rw = truncate_dw(right, w - 2, self.cw)
        ltext, lw = truncate_dw(left, w - 1 - rw, self.cw)
        bar = ltext + " " * max(0, w - lw - rw) + rtext
        try:
            self.scr.addstr(h - 1, 0, bar[:], curses.A_REVERSE)
        except curses.error:
            pass

    # ------------------------------------------------------------ 部品UI

    def getkey(self):
        try:
            return self.scr.get_wch()
        except curses.error:
            return None

    def pending_key(self):
        """Esc 直後の追加入力を確認する（Alt シーケンス判定用）"""
        self.scr.nodelay(True)
        try:
            k = self.scr.get_wch()
        except curses.error:
            k = None
        finally:
            self.scr.nodelay(False)
            self.scr.timeout(250)
        return k

    def is_bare_esc(self):
        """直前の Esc が単独押下なら True。
        続きがあれば端末のエスケープシーケンスとして読み捨てる
        （TERM 設定が実際の端末と違っても本文にゴミが入らないように）"""
        k = self.pending_key()
        if k is None:
            return True
        if k in ("[", "O"):
            self.scr.nodelay(True)
            try:
                while True:
                    try:
                        c = self.scr.get_wch()
                    except curses.error:
                        break
                    if isinstance(c, str) and c and "@" <= c <= "~":
                        break  # CSI 終端文字
            finally:
                self.scr.nodelay(False)
                self.scr.timeout(250)
        return False

    def prompt(self, label, init=""):
        """下段 1 行入力。確定で文字列、中止で None"""
        buf = list(init)
        pos = len(buf)
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            field = label + "".join(buf)
            line = pad_dw(field, w - 1, self.cw)
            try:
                self.scr.addstr(h - 1, 0, line, curses.A_REVERSE)
                cx = text_dw(label + "".join(buf[:pos]), self.cw)
                self.scr.move(h - 1, min(cx, w - 1))
            except curses.error:
                pass
            k = self.getkey()
            if k is None:
                continue
            if isinstance(k, str):
                if k == "\x1b":
                    if self.is_bare_esc():
                        return None
                elif k in ("\n", "\r"):
                    return "".join(buf)
                elif k in ("\x7f", "\x08"):
                    if pos > 0:
                        del buf[pos - 1]
                        pos -= 1
                elif k >= " " or k == "\t":
                    buf.insert(pos, k)
                    pos += 1
            else:
                if k == curses.KEY_ENTER:
                    return "".join(buf)
                elif k == curses.KEY_BACKSPACE:
                    if pos > 0:
                        del buf[pos - 1]
                        pos -= 1
                elif k == curses.KEY_DC:
                    if pos < len(buf):
                        del buf[pos]
                elif k == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif k == curses.KEY_RIGHT:
                    pos = min(len(buf), pos + 1)
                elif k == curses.KEY_HOME:
                    pos = 0
                elif k == curses.KEY_END:
                    pos = len(buf)

    def choice(self, message, keys):
        """下段にメッセージを表示し、keys のいずれかか Esc を待つ"""
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            line = pad_dw(" " + message, w - 1, self.cw)
            try:
                self.scr.addstr(h - 1, 0, line, curses.A_REVERSE)
            except curses.error:
                pass
            k = self.getkey()
            if k is None or not isinstance(k, str):
                continue
            if k == "\x1b":
                if self.is_bare_esc():
                    return "esc"
            elif k.lower() in keys:
                return k.lower()

    def msgbox(self, title, lines):
        """中央にメッセージボックスを表示。長文はスクロール可"""
        off = 0
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            box_w = min(w - 4, max(20, max(
                [text_dw(title, self.cw) + 2] +
                [text_dw(l, self.cw) for l in lines]) + 4))
            box_h = min(h - 4, len(lines) + 4)
            view_h = box_h - 4
            if view_h < 1:
                view_h = 1
                box_h = view_h + 4
            off = max(0, min(off, len(lines) - view_h))
            x0 = (w - box_w) // 2
            y0 = (h - box_h) // 2
            self.draw_box(y0, x0, box_h, box_w, title)
            for i in range(view_h):
                if off + i >= len(lines):
                    break
                t = pad_dw(" " + lines[off + i], box_w - 2, self.cw)
                try:
                    self.scr.addstr(y0 + 2 + i, x0 + 1, t)
                except curses.error:
                    pass
            foot = "Enter/Esc:閉じる"
            if len(lines) > view_h:
                foot = "↑↓:スクロール  " + foot
            t = pad_dw(" " + foot, box_w - 2, self.cw)
            try:
                self.scr.addstr(y0 + box_h - 1, x0 + 1, t, curses.A_REVERSE)
            except curses.error:
                pass
            curses.curs_set(0)
            k = self.getkey()
            curses.curs_set(1)
            if k is None:
                continue
            if k in ("\n", "\r", "q", " ") or k == curses.KEY_ENTER:
                return
            if k == "\x1b":
                if self.is_bare_esc():
                    return
            elif k == curses.KEY_UP:
                off = max(0, off - 1)
            elif k == curses.KEY_DOWN:
                off += 1
            elif k == curses.KEY_PPAGE:
                off = max(0, off - view_h)
            elif k == curses.KEY_NPAGE:
                off += view_h

    def draw_box(self, y0, x0, bh, bw, title):
        top = "+" + "-" * (bw - 2) + "+"
        try:
            self.scr.addstr(y0, x0, top)
            t, tw = truncate_dw(" " + title + " ", bw - 4, self.cw)
            self.scr.addstr(y0, x0 + 2, t, curses.A_REVERSE)
            for i in range(1, bh - 1):
                self.scr.addstr(y0 + i, x0, "|" + " " * (bw - 2) + "|")
            self.scr.addstr(y0 + bh - 1, x0, top)
        except curses.error:
            pass

    # ------------------------------------------------------------ メニュー

    def menu(self):
        mi = 0   # 上段の選択
        ii = 0   # ドロップダウン内の選択
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            # メニューバー
            try:
                self.scr.addstr(0, 0, " " * (w - 1), curses.A_REVERSE)
            except curses.error:
                pass
            x = 0
            xs = []
            for i, (title, _items) in enumerate(MENUS):
                label = " " + title + " "
                lw = text_dw(label, self.cw)
                xs.append(x)
                attr = curses.A_NORMAL if i == mi else curses.A_REVERSE
                try:
                    self.scr.addstr(0, x, label, attr)
                except curses.error:
                    pass
                x += lw
            # ドロップダウン
            items = MENUS[mi][1]
            iw = max(text_dw(l[0], self.cw) +
                     text_dw(l[1], self.cw) + 4
                     for l in items if l is not None)
            bw = iw + 2
            bx = min(xs[mi], max(0, w - bw - 1))
            try:
                self.scr.addstr(1, bx, "+" + "-" * iw + "+")
            except curses.error:
                pass
            for j, item in enumerate(items):
                y = 2 + j
                if item is None:
                    row = "+" + "-" * iw + "+"
                    attr = curses.A_NORMAL
                else:
                    label, accel, _ = item
                    aw = text_dw(accel, self.cw)
                    body = " " + label
                    body = pad_dw(body, iw - aw - 1, self.cw) + accel + " "
                    row = "|" + body + "|"
                    attr = curses.A_REVERSE if j == ii else curses.A_NORMAL
                try:
                    if item is None:
                        self.scr.addstr(y, bx, row)
                    else:
                        self.scr.addstr(y, bx, "|")
                        self.scr.addstr(y, bx + 1, body, attr)
                        self.scr.addstr(y, bx + 1 + iw, "|")
                except curses.error:
                    pass
            try:
                self.scr.addstr(2 + len(items), bx, "+" + "-" * iw + "+")
            except curses.error:
                pass
            curses.curs_set(0)
            k = self.getkey()
            curses.curs_set(1)
            if k is None:
                continue
            if k == "\x1b":
                if self.is_bare_esc():
                    return None
            elif k in ("\n", "\r") or k == curses.KEY_ENTER:
                item = items[ii]
                if item is not None:
                    return item[2]
            elif k == curses.KEY_LEFT:
                mi = (mi - 1) % len(MENUS)
                ii = self.first_item(MENUS[mi][1])
            elif k == curses.KEY_RIGHT:
                mi = (mi + 1) % len(MENUS)
                ii = self.first_item(MENUS[mi][1])
            elif k == curses.KEY_UP:
                ii = self.step_item(items, ii, -1)
            elif k == curses.KEY_DOWN:
                ii = self.step_item(items, ii, 1)

    @staticmethod
    def first_item(items):
        for j, it in enumerate(items):
            if it is not None:
                return j
        return 0

    @staticmethod
    def step_item(items, ii, d):
        n = len(items)
        j = ii
        for _ in range(n):
            j = (j + d) % n
            if items[j] is not None:
                return j
        return ii

    # ------------------------------------------------------------ ファイル選択

    def dialog_open_file(self):
        """ファイル選択ダイアログ。選ばれたパスか None を返す"""
        if self.path:
            d = os.path.dirname(self.path)
        else:
            d = os.getcwd()
        sel = 0
        off = 0
        while True:
            try:
                names = sorted(os.listdir(d), key=str.lower)
            except OSError as e:
                self.msgbox("エラー", [str(e)])
                return None
            dirs = [n for n in names
                    if os.path.isdir(os.path.join(d, n))
                    and not n.startswith(".")]
            files = [n for n in names
                     if not os.path.isdir(os.path.join(d, n))
                     and not n.startswith(".")]
            entries = [".."] + [n + "/" for n in dirs] + files

            self.draw()
            h, w = self.scr.getmaxyx()
            box_w = max(30, min(w - 4, 60))
            box_h = h - 4
            view_h = box_h - 4
            sel = max(0, min(sel, len(entries) - 1))
            if sel < off:
                off = sel
            if sel >= off + view_h:
                off = sel - view_h + 1
            x0 = (w - box_w) // 2
            y0 = 2
            title, _ = truncate_dw(d, box_w - 6, self.cw)
            self.draw_box(y0, x0, box_h, box_w, "開く: " + title)
            for i in range(view_h):
                j = off + i
                if j >= len(entries):
                    break
                t = pad_dw(" " + entries[j], box_w - 2, self.cw)
                attr = curses.A_REVERSE if j == sel else curses.A_NORMAL
                try:
                    self.scr.addstr(y0 + 2 + i, x0 + 1, t, attr)
                except curses.error:
                    pass
            foot = " Enter:決定  Esc:中止"
            t = pad_dw(foot, box_w - 2, self.cw)
            try:
                self.scr.addstr(y0 + box_h - 1, x0 + 1, t, curses.A_REVERSE)
            except curses.error:
                pass
            curses.curs_set(0)
            k = self.getkey()
            curses.curs_set(1)
            if k is None:
                continue
            if k == "\x1b":
                if self.is_bare_esc():
                    return None
            elif k == curses.KEY_UP:
                sel = max(0, sel - 1)
            elif k == curses.KEY_DOWN:
                sel = min(len(entries) - 1, sel + 1)
            elif k == curses.KEY_PPAGE:
                sel = max(0, sel - view_h)
            elif k == curses.KEY_NPAGE:
                sel = min(len(entries) - 1, sel + view_h)
            elif k in ("\n", "\r") or k == curses.KEY_ENTER:
                name = entries[sel]
                if name == "..":
                    d = os.path.dirname(d) or d
                    sel = off = 0
                elif name.endswith("/"):
                    d = os.path.join(d, name[:-1])
                    sel = off = 0
                else:
                    return os.path.join(d, name)

    # ------------------------------------------------------------ 検索

    def find_from(self, q, l, c, direction=1):
        """(行, 桁) から方向 direction に q を探す。全文一巡で終了"""
        if not q:
            return None
        n = len(self.lines)
        if direction > 0:
            idx = self.lines[l].find(q, c)
            if idx >= 0:
                return (l, idx)
            for i in range(1, n + 1):
                li = (l + i) % n
                idx = self.lines[li].find(q)
                if idx >= 0:
                    return (li, idx)
        else:
            idx = self.lines[l].rfind(q, 0, c)
            if idx >= 0:
                return (l, idx)
            for i in range(1, n + 1):
                li = (l - i) % n
                idx = self.lines[li].rfind(q)
                if idx >= 0:
                    return (li, idx)
        return None

    def find_forward(self, q, l, c):
        """(行, 桁) から文末方向に q を探す（折り返しなし）"""
        idx = self.lines[l].find(q, c)
        if idx >= 0:
            return (l, idx)
        for li in range(l + 1, len(self.lines)):
            idx = self.lines[li].find(q)
            if idx >= 0:
                return (li, idx)
        return None

    def goto_match(self, hit, q):
        self.cy, self.cx = hit
        self.anchor = (hit[0], hit[1] + len(q))
        self.want_x = None

    # ------------------------------------------------------------ アクション

    def confirm_discard(self):
        """変更破棄の確認。続行してよければ True"""
        if not self.dirty:
            return True
        c = self.choice("変更を保存しますか？  (y)保存 (n)保存しない (Esc)キャンセル",
                        ("y", "n"))
        if c == "y":
            return self.save_file()
        return c == "n"

    def act_quit(self):
        if self.confirm_discard():
            self.quitting = True

    def act_file_save(self):
        if self.save_file():
            self.flash_msg("保存しました")

    def act_file_saveas(self):
        init = self.path or os.path.join(os.getcwd(), "")
        p = self.prompt("名前を付けて保存: ", init)
        if not p:
            return False
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.exists(p) and p != self.path:
            c = self.choice("既にあります。上書きしますか？ (y/n)", ("y", "n"))
            if c != "y":
                return False
        ok = self.save_file(p)
        if ok:
            self.flash_msg("保存しました")
        return ok

    def act_file_new(self):
        if not self.confirm_discard():
            return
        self.lines = [""]
        self.path = None
        self._saved_text = ""
        self._disk_data = None
        self.encoding = "utf-8"
        self.crlf = False
        self.dirty = False
        self._autosave_failed = False
        self.version += 1
        self.cy = self.cx = 0
        self.top = (0, 0)
        self.anchor = None
        self.undo_stack = []
        self.redo_stack = []
        self.last_kind = None

    def act_file_open(self):
        if not self.confirm_discard():
            return
        p = self.dialog_open_file()
        if p:
            try:
                self.load_file(p)
            except OSError as e:
                self.msgbox("読み込みエラー", [str(e)])

    def act_undo(self):
        if not self.undo_stack:
            self.flash_msg("これ以上戻れません")
            return
        self.redo_stack.append(self.snapshot())
        self.restore(self.undo_stack.pop())
        self.last_kind = None

    def act_redo(self):
        if not self.redo_stack:
            self.flash_msg("やり直す操作がありません")
            return
        self.undo_stack.append(self.snapshot())
        self.restore(self.redo_stack.pop())
        self.last_kind = None

    def act_cut(self):
        text = self.selection_text()
        if not text:
            self.flash_msg("範囲が選択されていません")
            return
        self.clip = text
        self.checkpoint("block")
        self.delete_selection()
        self.flash_msg("%d字を切り取りました" % len(text.replace("\n", "")))

    def act_copy(self):
        text = self.selection_text()
        if not text:
            self.flash_msg("範囲が選択されていません")
            return
        self.clip = text
        self.anchor = None
        self.flash_msg("%d字をコピーしました" % len(text.replace("\n", "")))

    def act_paste(self):
        if not self.clip:
            self.flash_msg("貼り付けるものがありません")
            return
        self.edit_insert(self.clip, "block")

    def act_select_all(self):
        self.anchor = (0, 0)
        self.cy = len(self.lines) - 1
        self.cx = len(self.lines[self.cy])
        self.want_x = None

    def act_toggle_mark(self):
        if self.anchor is None:
            self.anchor = (self.cy, self.cx)
            self.flash_msg("選択開始（Escで解除）")
        else:
            self.anchor = None
            self.flash_msg("選択解除")

    def act_insert_date(self):
        now = datetime.datetime.now()
        s = "%d/%02d/%02d(%s) %02d:%02d" % (
            now.year, now.month, now.day,
            WEEKDAYS_JA[now.weekday()], now.hour, now.minute)
        self.edit_insert(s, "block")

    def act_search(self):
        q = self.prompt("検索: ", self.search_q)
        if not q:
            return
        self.search_q = q
        hit = self.find_from(q, self.cy, self.cx)
        if hit:
            self.goto_match(hit, q)
        else:
            self.flash_msg("見つかりません")

    def act_search_next(self):
        if not self.search_q:
            self.act_search()
            return
        hit = self.find_from(self.search_q, self.cy, self.cx + 1)
        if hit:
            self.goto_match(hit, self.search_q)
        else:
            self.flash_msg("見つかりません")

    def act_search_prev(self):
        if not self.search_q:
            self.act_search()
            return
        hit = self.find_from(self.search_q, self.cy, self.cx, -1)
        if hit:
            self.goto_match(hit, self.search_q)
        else:
            self.flash_msg("見つかりません")

    def act_replace(self):
        q = self.prompt("置換・検索文字列: ", self.search_q)
        if not q:
            return
        r = self.prompt("置換後の文字列: ", self.replace_q)
        if r is None:
            return
        self.search_q = q
        self.replace_q = r
        count = 0
        replace_all = False
        ch = "n"
        pos = (self.cy, self.cx)
        while True:
            hit = self.find_forward(q, pos[0], pos[1])
            if hit is None:
                break
            self.goto_match(hit, q)
            if not replace_all:
                ch = self.choice(
                    "置換しますか？  (y)置換 (n)スキップ (a)以降すべて (Esc)終了",
                    ("y", "n", "a"))
                if ch == "esc":
                    break
                if ch == "a":
                    replace_all = True
            if replace_all or ch == "y":
                self.checkpoint("block")
                self.anchor = None
                s = self.lines[hit[0]]
                self.lines[hit[0]] = s[:hit[1]] + r + s[hit[1] + len(q):]
                self.cy, self.cx = hit[0], hit[1] + len(r)
                self.mark_changed()
                count += 1
                pos = (self.cy, self.cx)
            else:
                self.anchor = None
                pos = (hit[0], hit[1] + len(q))
        self.anchor = None
        self.flash_msg("%d件置換しました（カーソル位置から文末まで）" % count)

    def act_jump(self):
        s = self.prompt("行番号: ")
        if not s:
            return
        try:
            n = int(s)
        except ValueError:
            self.flash_msg("数値を入力してください")
            return
        self.cy = max(0, min(len(self.lines) - 1, n - 1))
        self.cx = 0
        self.want_x = None

    def act_goto_top(self):
        self.cy = self.cx = 0
        self.want_x = None

    def act_goto_bottom(self):
        self.cy = len(self.lines) - 1
        self.cx = len(self.lines[self.cy])
        self.want_x = None

    def act_count(self):
        total = self.total_chars()
        nl = len(self.lines) - 1
        info = [
            "文字数（改行含まず）  %s" % format(total, ","),
            "文字数（改行含む）    %s" % format(total + nl, ","),
            "行数                  %s" % format(len(self.lines), ","),
            "原稿用紙換算(400字)   約%s枚" % format((total + 399) // 400, ","),
        ]
        seltext = self.selection_text()
        if seltext:
            info.append("")
            info.append("選択範囲              %s字" %
                        format(len(seltext.replace("\n", "")), ","))
        self.msgbox("文字数カウント", info)

    def act_toggle_autosave(self):
        self.config["autosave"] = not self.config["autosave"]
        self.save_config()
        self.flash_msg("自動保存: %s" %
                       ("ON" if self.config["autosave"] else "OFF"))

    def act_toggle_ambw(self):
        self.config["ambiguous_wide"] = not self.config["ambiguous_wide"]
        self.save_config()
        self.cw = make_char_width(self.config["ambiguous_wide"],
                                  self.config["tab_width"])
        self.reset_wrap()
        self.flash_msg("曖昧幅文字: %s" %
                       ("全角" if self.config["ambiguous_wide"] else "半角"))

    def act_help(self):
        self.msgbox("キー操作一覧", HELP_LINES)

    def act_about(self):
        self.msgbox("pome について", [
            "pome " + VERSION,
            "ポメラ風コマンドラインエディタ",
            "",
            "設定ファイル: " + CONFIG_PATH,
        ])

    # ------------------------------------------------------------ Git

    def git_repo_dir(self):
        d = os.path.dirname(self.path) if self.path else os.getcwd()
        try:
            p = subprocess.run(
                ["git", "-C", d, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if p.returncode != 0:
            return None
        return p.stdout.strip()

    def git_run(self, repo, args):
        return subprocess.run(
            ["git", "-C", repo] + args,
            capture_output=True, text=True,
            timeout=self.config["git_timeout_sec"])

    @staticmethod
    def cp_lines(label, p):
        out = ["$ " + label]
        for stream in (p.stdout, p.stderr):
            for l in (stream or "").splitlines():
                out.append("  " + l)
        return out

    def manuscript_matches_disk(self):
        """Compare the exact saved snapshot to on-disk bytes (including deletion)."""
        if not self.path:
            return True
        try:
            with open(self.path, "rb") as f:
                on_disk = f.read()
        except FileNotFoundError:
            on_disk = None
        return on_disk == self._disk_data

    def act_git_push(self):
        """Commit only the current manuscript; reject stale files and foreign staging."""
        if self.path is None or self.dirty:
            if not self.save_file():
                return
        repo = self.git_repo_dir()
        if repo is None:
            self.msgbox("Git", ["Gitリポジトリの中にあるファイルではありません。",
                                "リポジトリ内に保存してから実行してください。"])
            return
        self.flash_now("Git 送信中...（テザリング接続を確認してください）")
        out = []
        ok = False
        try:
            # Even a clean pome buffer may be stale if another app replaced
            # the manuscript. Do not silently commit what is not on screen.
            if not self.manuscript_matches_disk():
                raise RuntimeError(
                    "原稿が外部で変更または削除されています。画面とディスクの内容が一致しないため"
                    "送信を中止しました。別名保存または再読込してから送信してください。")

            expected = os.path.relpath(self.path, repo)
            staged = self.git_run(repo, ["diff", "--cached", "--name-only", "-z"])
            if staged.returncode != 0:
                out.extend(self.cp_lines("git diff --cached", staged))
                raise RuntimeError("ステージ済みファイルの確認に失敗しました。")
            paths = [p for p in staged.stdout.split("\0") if p]
            if paths not in ([], [expected]):
                raise RuntimeError(
                    "別のファイルがステージされています。誤コミット防止のため送信を中止しました。"
                    "ターミナルで git status を確認してください。")

            if paths == [expected]:
                # A previous failed commit may leave *our* file staged.
                # Retry safely only when its staged content matches disk;
                # never overwrite intentionally partially staged content.
                p = self.git_run(repo, ["diff", "--quiet", "--", self.path])
                if p.returncode != 0:
                    raise RuntimeError(
                        "原稿のステージ内容とディスクの内容が一致しません。"
                        "既存のステージを変更せず中止しました。git diff --cached と"
                        " git diff で確認してください。")
                out.append("前回の失敗でステージされた原稿を確認しました。安全に再試行します。")

            p = self.git_run(repo, ["add", "--", self.path])
            out.extend(self.cp_lines("git add（現在の原稿のみ）", p))
            if p.returncode != 0:
                raise RuntimeError("git addに失敗したため、コミット・送信はしません。")

            staged = self.git_run(repo, ["diff", "--cached", "--name-only", "-z"])
            if staged.returncode != 0:
                out.extend(self.cp_lines("git diff --cached", staged))
                raise RuntimeError("コミット対象の確認に失敗しました。")
            paths = [p for p in staged.stdout.split("\0") if p]
            if paths not in ([], [expected]):
                raise RuntimeError(
                    "別ファイルがステージに混入しました。自動的に解除せず中止しました。"
                    "git status を確認してください。")

            # Check again after add to catch an intervening external rewrite.
            if not self.manuscript_matches_disk():
                raise RuntimeError(
                    "送信処理中に原稿が外部で変更されました。"
                    "ステージした内容は保持して中止しました。git status を確認してください。")

            if paths:
                msg = "pome: update %s (%s)" % (
                    os.path.basename(self.path),
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
                p = self.git_run(
                    repo, ["commit", "--only", "-m", msg, "--", self.path])
                out.extend(self.cp_lines("git commit（現在の原稿のみ）", p))
                if p.returncode != 0:
                    raise RuntimeError(
                        "git commitに失敗しました。pushは実行しません。"
                        "ステージされた原稿だけならpomeの送信を再実行できます。"
                        "それ以外は git status を確認してください。")
            else:
                out.append("現在の原稿にコミットすべき変更はありません。")

            p = self.git_run(repo, ["push"])
            out.extend(self.cp_lines("git push", p))
            if p.returncode != 0:
                raise RuntimeError(
                    "git pushに失敗しました。ローカルコミットは保持されています。"
                    "通信復旧後、送信を再実行できます。")
            ok = True
        except subprocess.TimeoutExpired:
            out.append("時間切れです。接続を確認してください。")
        except (OSError, RuntimeError) as e:
            out.append(str(e))
        self.flash = None
        self.msgbox("Git プッシュ " + ("完了" if ok else "失敗"), out)

    def act_git_pull(self):
        if self.dirty:
            c = self.choice("未保存の変更があります。保存してから受信しますか？ (y/Esc)",
                            ("y",))
            if c != "y" or not self.save_file():
                return
        repo = self.git_repo_dir()
        if repo is None:
            self.msgbox("Git", ["Gitリポジトリの中にあるファイルではありません。"])
            return
        self.flash_now("Git 受信中...（テザリング接続を確認してください）")
        out = []
        ok = False
        try:
            # 別プロセスによる原稿変更を読み落としたまま受信しない。
            if self.path:
                if not self.manuscript_matches_disk():
                    raise RuntimeError(
                        "原稿が外部で変更または削除されています。再読込を確認してから受信してください。")

            status = self.git_run(
                repo, ["status", "--porcelain", "--untracked-files=no"])
            if status.returncode != 0:
                out.extend(self.cp_lines("git status", status))
                raise RuntimeError("作業状態を確認できないため受信を中止しました。")
            if status.stdout.strip():
                raise RuntimeError(
                    "未コミットの変更があります。送信・コミットまたは退避してから受信してください。")

            p = self.git_run(repo, ["pull", "--ff-only"])
            out.extend(self.cp_lines("git pull --ff-only", p))
            if p.returncode != 0:
                raise RuntimeError(
                    "受信に失敗しました。競合を自動解消せず、原稿は上書きしません。")
            ok = True

            if self.path:
                try:
                    with open(self.path, "rb") as f:
                        current = f.read()
                except FileNotFoundError:
                    out.append("受信後、開いていた原稿が見つかりません。"
                               "現在の表示内容は保持しました。")
                else:
                    if current != self._disk_data:
                        old_y, old_x = self.cy, self.cx
                        self.load_file(self.path)
                        self.cy = min(old_y, len(self.lines) - 1)
                        self.cx = min(old_x, len(self.lines[self.cy]))
                        out.append("更新された原稿を読み直しました。")
                    else:
                        out.append("現在の原稿は変更されていません。編集位置を保持しました。")
        except subprocess.TimeoutExpired:
            out.append("時間切れです。接続を確認してください。")
        except (OSError, RuntimeError) as e:
            out.append(str(e))
            ok = False
        self.flash = None
        self.msgbox("Git プル " + ("完了" if ok else "失敗"), out)

    def act_git_status(self):
        repo = self.git_repo_dir()
        if repo is None:
            self.msgbox("Git", ["Gitリポジトリの中にあるファイルではありません。"])
            return
        out = []
        try:
            p = self.git_run(repo, ["status", "-sb"])
            out += self.cp_lines("git status -sb", p)
            p = self.git_run(repo, ["log", "--oneline", "-5"])
            out += self.cp_lines("git log --oneline -5", p)
        except (OSError, subprocess.TimeoutExpired) as e:
            out.append(str(e))
        self.msgbox("Git 状態", out)

    # ------------------------------------------------------------ 入力処理

    def dispatch(self, name):
        getattr(self, "act_" + name)()

    def handle(self, k):
        if isinstance(k, str):
            if k == "\x1b":
                if not self.is_bare_esc():
                    return  # Alt や未解釈の端末シーケンスは本文に入れない
                if self.anchor is not None:
                    self.anchor = None
                else:
                    action = self.menu()
                    if action:
                        self.dispatch(action)
            elif k in ("\n", "\r"):
                self.edit_insert("\n", "type")
            elif k in CTRL_MAP:
                self.dispatch(CTRL_MAP[k])
            elif k in ("\x7f", "\x08"):
                self.backspace()
            elif k == "\t" or k >= " ":
                self.edit_insert(k, "type")
            return

        if k == curses.KEY_UP:
            self.move_vert(-1)
        elif k == curses.KEY_DOWN:
            self.move_vert(1)
        elif k == curses.KEY_LEFT:
            self.move_left()
        elif k == curses.KEY_RIGHT:
            self.move_right()
        elif k == curses.KEY_HOME:
            self.move_home()
        elif k == curses.KEY_END:
            self.move_end()
        elif k == curses.KEY_PPAGE:
            self.move_page(-1)
        elif k == curses.KEY_NPAGE:
            self.move_page(1)
        elif k == curses.KEY_DC:
            self.delete_char()
        elif k == curses.KEY_BACKSPACE:
            self.backspace()
        elif k == curses.KEY_ENTER:
            self.edit_insert("\n", "type")
        elif k == curses.KEY_SLEFT:
            if self.anchor is None:
                self.anchor = (self.cy, self.cx)
            self.move_left()
        elif k == curses.KEY_SRIGHT:
            if self.anchor is None:
                self.anchor = (self.cy, self.cx)
            self.move_right()
        elif k in (curses.KEY_SR, 337):
            if self.anchor is None:
                self.anchor = (self.cy, self.cx)
            self.move_vert(-1)
        elif k in (curses.KEY_SF, 336):
            if self.anchor is None:
                self.anchor = (self.cy, self.cx)
            self.move_vert(1)
        elif k == curses.KEY_RESIZE:
            pass

    def tick(self):
        """入力タイムアウト時の処理（自動保存など）"""
        if (self.config["autosave"] and not self._autosave_failed
                and self.dirty and self.path
                and time.time() - self.last_change_time
                >= self.config["autosave_idle_sec"]):
            if self.save_file():
                self.flash_msg("自動保存しました", 1.5)
            else:
                self._autosave_failed = True
                self.flash_msg("自動保存を一時停止しました。Ctrl+Sまたは名前を付けて保存で再開", 6)

    # ------------------------------------------------------------ メインループ

    def run(self, scr):
        self.scr = scr
        curses.raw()
        curses.curs_set(1)
        scr.keypad(True)
        scr.timeout(250)
        # ^S / ^Q をフロー制御に取られないようにする
        try:
            import termios
            fd = sys.stdin.fileno()
            attrs = termios.tcgetattr(fd)
            attrs[0] &= ~(termios.IXON | termios.IXOFF)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except Exception:
            pass

        while not self.quitting:
            self.draw()
            k = self.getkey()
            if k is None:
                self.tick()
                continue
            self.handle(k)

        self.config["last_file"] = self.path or ""
        self.save_config()


def main():
    os.environ.setdefault("ESCDELAY", "25")
    locale.setlocale(locale.LC_ALL, "")

    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args and args[0] in ("-v", "--version"):
        print("pome " + VERSION)
        return

    ed = Editor(args[0] if args else None)
    try:
        curses.wrapper(ed.run)
    except Exception:
        rescue = ed.emergency_save()
        if rescue:
            sys.stderr.write("編集内容を退避しました: %s\n" % rescue)
        raise


if __name__ == "__main__":
    main()
