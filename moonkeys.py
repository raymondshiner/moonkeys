#!/usr/bin/env python3
"""moonkeys — one-stop keyboard + hotkey customizer.

Two sections share one popup:
  - Keyboard: local-Oryx editor for the Moonlander keymap.c — per-key Tap /
    Double-tap / Hold behaviour, per-key RGB, compile + flash.
  - Hotkeys:  searchable, modifier-sorted registry of every hyprland.conf bind,
    with inline label editing.

The two are cross-referenced: a QMK tap chord like LGUI(KC_S) decodes to Super+S
and is matched against the Hyprland binds, so each side shows what the other does.
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell  # noqa: E402

import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from hyprkeys_parser import parse_config, display_label  # noqa: E402
from hyprkeys_layout import (  # noqa: E402
    BG, BG_ELEV, MUTED, TEXT, CYAN, YELLOW, RED, PURPLE,
)
import moonkeys_qmk as q  # noqa: E402
from moonkeys_view import QmkView  # noqa: E402
from moonkeys_hotkeys import HotkeysList  # noqa: E402

PID_FILE = '/tmp/moonkeys.pid'
LAYER_NAMES = {0: 'Default', 1: 'Other', 2: 'Apps'}


CSS = f"""
window {{ background: transparent; }}
.popup-inner {{
    background-color: {BG};
    border-radius: 14px;
    margin: 16px;
    padding: 20px;
    box-shadow: 0 0 0 1px {PURPLE}, 0 0 32px {PURPLE};
}}
.app-title {{ color: {PURPLE}; font-family: "JetBrainsMono Nerd Font"; font-size: 16px; font-weight: bold; }}
.muted {{ color: {MUTED}; font-family: "JetBrainsMono Nerd Font"; font-size: 11px; }}
.search-entry {{
    background-color: {BG_ELEV}; color: {TEXT};
    border-radius: 8px; padding: 6px 10px;
    border: 1px solid {MUTED};
    font-family: "JetBrainsMono Nerd Font"; font-size: 13px;
}}
.search-entry:focus {{ border-color: {PURPLE}; }}
.chip {{
    background-color: {BG_ELEV}; color: {MUTED};
    border-radius: 999px; padding: 4px 12px; border: none;
    font-family: "JetBrainsMono Nerd Font"; font-size: 11px;
    box-shadow: none; text-shadow: none;
}}
.chip:hover {{ color: {TEXT}; }}
.chip.active {{ background-color: {PURPLE}; color: {BG}; }}
.detail-key {{ color: {PURPLE}; font-family: "JetBrainsMono Nerd Font"; font-size: 13px; font-weight: bold; }}
.detail-cmd {{ color: {MUTED}; font-family: "JetBrainsMono Nerd Font"; font-size: 11px; }}
.label-entry {{
    background-color: {BG_ELEV}; color: {TEXT};
    border-radius: 6px; padding: 5px 9px;
    border: 1px solid {MUTED};
    font-family: "JetBrainsMono Nerd Font"; font-size: 12px;
}}
.label-entry:focus {{ border-color: {PURPLE}; }}
.label-entry.unsaved {{ border-color: {YELLOW}; }}
.save-btn {{
    background-color: {CYAN}; color: {BG};
    border-radius: 8px; padding: 6px 14px;
    font-family: "JetBrainsMono Nerd Font"; font-size: 12px; font-weight: bold;
    border: none; box-shadow: none; text-shadow: none;
}}
.save-btn:disabled {{ background-color: {BG_ELEV}; color: {MUTED}; }}
.flash-btn {{
    background-color: {PURPLE}; color: {BG};
    border-radius: 8px; padding: 6px 14px;
    font-family: "JetBrainsMono Nerd Font"; font-size: 12px; font-weight: bold;
    border: none; box-shadow: none; text-shadow: none;
}}
.flash-btn:hover {{ box-shadow: 0 0 10px {PURPLE}; }}
.close-btn {{
    background: transparent; color: {MUTED}; border: none; padding: 2px 8px;
    font-family: "JetBrainsMono Nerd Font"; font-size: 16px;
    box-shadow: none; text-shadow: none;
}}
.close-btn:hover {{ color: {RED}; }}
.toast {{ color: {CYAN}; font-family: "JetBrainsMono Nerd Font"; font-size: 11px; }}
.half-label {{ color: {MUTED}; font-family: "JetBrainsMono Nerd Font"; font-size: 10px; }}
.mode-chip {{
    background-color: {BG_ELEV}; color: {MUTED};
    border-radius: 999px; padding: 4px 14px; border: none;
    font-family: "JetBrainsMono Nerd Font"; font-size: 11px; font-weight: bold;
    box-shadow: none; text-shadow: none;
}}
.mode-chip:hover {{ color: {TEXT}; }}
.mode-chip.active {{ background-color: {PURPLE}; color: {BG}; }}
.qkey {{
    background-color: {BG_ELEV}; color: {TEXT};
    border-radius: 6px; border: 1px solid {MUTED}; padding: 3px;
    font-family: "JetBrainsMono Nerd Font"; font-size: 11px;
    box-shadow: none; text-shadow: none;
}}
.qkey.transparent {{ color: {MUTED}; border-color: rgba(103, 118, 145, 0.4); background-color: {BG}; }}
.qkey.selected {{ border: 2px solid {PURPLE}; }}
.qkey.unsaved {{ border-color: {YELLOW}; }}
.qcap {{ font-family: "JetBrainsMono Nerd Font"; font-size: 11px; }}
.qbadge {{ font-family: "JetBrainsMono Nerd Font"; font-size: 8px; color: {CYAN}; }}
.xref {{ color: {CYAN}; font-family: "JetBrainsMono Nerd Font"; font-size: 10px; }}
.drawer {{
    background-color: {BG_ELEV}; border-radius: 10px; padding: 14px;
    margin-left: 4px;
}}
.swatch-btn {{ border-radius: 4px; padding: 0; }}
.group-header {{ color: {PURPLE}; font-family: "JetBrainsMono Nerd Font"; font-size: 12px; font-weight: bold; }}
.hk-row {{ padding: 2px 4px; }}
.hk-chip {{ color: {TEXT}; font-family: "JetBrainsMono Nerd Font"; font-size: 12px; font-weight: bold; }}
"""


class MoonKeys(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.binds, _ = parse_config()
        self.mode = 'keyboard'
        self.qmk_count = 0
        self.hotkeys_count = 0
        self.mode_chips = {}

        self._setup_window()
        self._build_ui()
        self._wire_cross_reference()
        self.show_all()
        self.stack.set_visible_child_name(self.mode)

    # ── window setup ────────────────────────────────────────────────────────
    def _setup_window(self):
        self.set_decorated(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.connect('key-press-event', self._on_key)
        self.connect('destroy', self._on_destroy)

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_valign(Gtk.Align.CENTER)
        self.add(outer)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.get_style_context().add_class('popup-inner')
        inner.set_size_request(1120, -1)
        outer.add(inner)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title = Gtk.Label(label='moonkeys')
        title.get_style_context().add_class('app-title')
        header.pack_start(title, False, False, 0)
        self.sub_lbl = Gtk.Label(label='')
        self.sub_lbl.get_style_context().add_class('muted')
        header.pack_start(self.sub_lbl, False, False, 0)

        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        mode_row.set_margin_start(12)
        for label, mode in (('Keyboard', 'keyboard'), ('Hotkeys', 'hotkeys')):
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class('mode-chip')
            if mode == self.mode:
                btn.get_style_context().add_class('active')
            btn.connect('clicked', lambda _b, m=mode: self._set_mode(m))
            self.mode_chips[mode] = btn
            mode_row.pack_start(btn, False, False, 0)
        header.pack_start(mode_row, False, False, 0)

        header.pack_start(Gtk.Box(), True, True, 0)
        self.toast_lbl = Gtk.Label(label='')
        self.toast_lbl.get_style_context().add_class('toast')
        header.pack_start(self.toast_lbl, False, False, 0)
        self.flash_btn = Gtk.Button(label=' Flash')
        self.flash_btn.get_style_context().add_class('flash-btn')
        self.flash_btn.set_tooltip_text('Compile + flash the Moonlander firmware')
        self.flash_btn.connect('clicked', lambda _b: self.qmk.flash(self))
        header.pack_start(self.flash_btn, False, False, 0)
        self.save_btn = Gtk.Button(label='Save 0 changes')
        self.save_btn.get_style_context().add_class('save-btn')
        self.save_btn.set_sensitive(False)
        self.save_btn.connect('clicked', lambda _b: self._do_save())
        header.pack_start(self.save_btn, False, False, 0)
        close = Gtk.Button(label='✕')
        close.get_style_context().add_class('close-btn')
        close.connect('clicked', lambda _b: self._request_close())
        header.pack_start(close, False, False, 0)
        inner.pack_start(header, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        inner.pack_start(self.stack, True, True, 0)

        self.qmk = QmkView(self._toast)
        self.qmk.set_count_callback(self._on_qmk_count)
        self.qmk.set_saved_callback(self._after_save)
        self.stack.add_named(self.qmk, 'keyboard')

        self.hotkeys = HotkeysList(self._toast)
        self.hotkeys.set_count_callback(self._on_hotkeys_count)
        self.hotkeys.set_saved_callback(self._after_save)
        self.stack.add_named(self.hotkeys, 'hotkeys')

        self._update_subtitle()

    # ── cross-reference plumbing ────────────────────────────────────────────
    def _wire_cross_reference(self):
        self.qmk.set_hypr_index(self.binds, display_label)
        self.hotkeys.set_send_index(self._build_send_index(self.qmk.model))

    def _after_save(self):
        self.binds, _ = parse_config()
        self.qmk.set_hypr_index(self.binds, display_label)
        self.hotkeys.reload()
        self.hotkeys.set_send_index(self._build_send_index(self.qmk.model))
        self._refresh_save_btn()

    def _build_send_index(self, model):
        index = {}
        for layer, keys in model['layers'].items():
            for k in keys:
                slots = q.decode_slots(model, k['keycode'])
                chord = q.chord_to_hypr(slots.get('tap'))
                if not chord:
                    continue
                base = model['layers'][0][k['idx']]['keycode']
                cap = q.cap_label(model, base) or f"pos{k['idx']}"
                cap = cap.replace('\n', ' ')
                if layer != 0:
                    cap = f'{cap} ({LAYER_NAMES.get(layer, layer)})'
                index.setdefault(chord, [])
                if cap not in index[chord]:
                    index[chord].append(cap)
        return index

    # ── mode switching ──────────────────────────────────────────────────────
    def _set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        self.stack.set_visible_child_name(mode)
        for m, btn in self.mode_chips.items():
            ctx = btn.get_style_context()
            (ctx.add_class if m == mode else ctx.remove_class)('active')
        self.flash_btn.set_visible(mode == 'keyboard')
        self._update_subtitle()
        self._refresh_save_btn()
        if mode == 'hotkeys':
            self.hotkeys.focus_search()

    def _update_subtitle(self):
        self.sub_lbl.set_text(
            'moonlander firmware — tap · double · hold + RGB'
            if self.mode == 'keyboard'
            else 'system hotkey registry — search · sort · relabel')

    def _on_qmk_count(self, n):
        self.qmk_count = n
        if self.mode == 'keyboard':
            self._refresh_save_btn()

    def _on_hotkeys_count(self, n):
        self.hotkeys_count = n
        if self.mode == 'hotkeys':
            self._refresh_save_btn()

    def _refresh_save_btn(self):
        n = self.qmk_count if self.mode == 'keyboard' else self.hotkeys_count
        self.save_btn.set_label(f'Save {n} change{"s" if n != 1 else ""}')
        self.save_btn.set_sensitive(n > 0)

    # ── save ────────────────────────────────────────────────────────────────
    def _do_save(self):
        if self.mode == 'keyboard':
            self.qmk.save(self)
        else:
            self.hotkeys.save(self)
        self._refresh_save_btn()

    def _toast(self, text, error=False):
        self.toast_lbl.set_text(text)
        if error:
            rgba = Gdk.RGBA()
            rgba.parse(RED)
            self.toast_lbl.override_color(Gtk.StateFlags.NORMAL, rgba)
        GLib.timeout_add(3500, lambda: self.toast_lbl.set_text('') or False)

    # ── close ────────────────────────────────────────────────────────────────
    def _request_close(self):
        n = self.qmk_count if self.mode == 'keyboard' else self.hotkeys_count
        if n == 0:
            self.destroy()
            return
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f'Discard {n} unsaved change{"s" if n != 1 else ""}?',
        )
        dlg.add_button('Cancel', Gtk.ResponseType.CANCEL)
        dlg.add_button('Discard', Gtk.ResponseType.NO)
        dlg.add_button('Save and close', Gtk.ResponseType.YES)
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            self._do_save()
            self.destroy()
        elif resp == Gtk.ResponseType.NO:
            self.destroy()

    def _on_key(self, _w, ev):
        kv = ev.keyval
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        if kv == Gdk.KEY_Escape:
            self._request_close()
            return True
        if ctrl and kv in (Gdk.KEY_m, Gdk.KEY_M):
            self._set_mode('hotkeys' if self.mode == 'keyboard' else 'keyboard')
            return True
        if ctrl and kv in (Gdk.KEY_s, Gdk.KEY_S):
            self._do_save()
            return True
        if self.mode == 'keyboard':
            if ctrl and Gdk.KEY_1 <= kv <= Gdk.KEY_3:
                self.qmk._on_layer(None, kv - Gdk.KEY_1)
                return True
            return False
        if kv == Gdk.KEY_slash and not self.hotkeys.search_entry.is_focus():
            self.hotkeys.focus_search()
            return True
        return False

    def _on_destroy(self, _w):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        Gtk.main_quit()


def main():
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read().strip())
            os.kill(pid, signal.SIGTERM)
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
            return
        except (ProcessLookupError, ValueError, OSError):
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    MoonKeys()
    Gtk.main()


if __name__ == '__main__':
    main()
