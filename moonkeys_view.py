"""Keyboard (QMK / Moonlander firmware) view — the moonkeys backend UI.

A Gtk.Box embedded in the moonkeys popup. Renders the live keymap.c on a
Moonlander-shaped grid (cap legend + per-key RGB swatch), with a per-key side
drawer for editing the key's label, its behaviour (Tap / Double-tap / Hold), and
its HSV colour.

The Tap / Double-tap fields are searchable and populate from the labelled
Hyprland hotkeys — pick "Smith" and the field resolves to the chord LGUI(KC_S).
A key's label is unified: if it drives a labelled hotkey, editing the label
renames that hotkey in hyprland.conf; otherwise it is stored in the keymap.c
managed-label region.
"""
import subprocess

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango  # noqa: E402

import moonkeys_qmk as q
import moonkeys_flash as flash
from hyprkeys_parser import display_label, write_labels

# (LAYOUT idx, grid row, grid col) per half — derived from keyboard.json geometry.
LEFT_MAIN = [
    (0, 0, 0), (1, 0, 1), (2, 0, 2), (3, 0, 3), (4, 0, 4), (5, 0, 5), (6, 0, 6),
    (14, 1, 0), (15, 1, 1), (16, 1, 2), (17, 1, 3), (18, 1, 4), (19, 1, 5), (20, 1, 6),
    (28, 2, 0), (29, 2, 1), (30, 2, 2), (31, 2, 3), (32, 2, 4), (33, 2, 5), (34, 2, 6),
    (42, 3, 0), (43, 3, 1), (44, 3, 2), (45, 3, 3), (46, 3, 4), (47, 3, 5),
    (54, 4, 0), (55, 4, 1), (56, 4, 2), (57, 4, 3), (58, 4, 4),
]
RIGHT_MAIN = [
    (7, 0, 0), (8, 0, 1), (9, 0, 2), (10, 0, 3), (11, 0, 4), (12, 0, 5), (13, 0, 6),
    (21, 1, 0), (22, 1, 1), (23, 1, 2), (24, 1, 3), (25, 1, 4), (26, 1, 5), (27, 1, 6),
    (35, 2, 0), (36, 2, 1), (37, 2, 2), (38, 2, 3), (39, 2, 4), (40, 2, 5), (41, 2, 6),
    (48, 3, 1), (49, 3, 2), (50, 3, 3), (51, 3, 4), (52, 3, 5), (53, 3, 6),
    (61, 4, 2), (62, 4, 3), (63, 4, 4), (64, 4, 5), (65, 4, 6),
]
LEFT_THUMB = [(59, 0, 1), (66, 1, 0), (67, 1, 1), (68, 1, 2)]
RIGHT_THUMB = [(60, 0, 1), (69, 1, 0), (70, 1, 1), (71, 1, 2)]

LAYER_CHIPS = [('Default', 0), ('Other', 1), ('Apps', 2)]

HOLD_OPTIONS = [
    ('— none —', None),
    ('Layer: Default', ('layer', '_DEFAULT')),
    ('Layer: Other', ('layer', '_OTHER')),
    ('Layer: Apps', ('layer', '_APPS')),
    ('Mod: Super', ('mod', 'Super')),
    ('Mod: Shift', ('mod', 'Shift')),
    ('Mod: Ctrl', ('mod', 'Ctrl')),
    ('Mod: Alt', ('mod', 'Alt')),
]

CURATED_KEYCODES = [
    'KC_TRANSPARENT', 'KC_NO',
    'KC_A', 'KC_B', 'KC_C', 'KC_D', 'KC_E', 'KC_F', 'KC_G', 'KC_H', 'KC_I',
    'KC_J', 'KC_K', 'KC_L', 'KC_M', 'KC_N', 'KC_O', 'KC_P', 'KC_Q', 'KC_R',
    'KC_S', 'KC_T', 'KC_U', 'KC_V', 'KC_W', 'KC_X', 'KC_Y', 'KC_Z',
    'KC_1', 'KC_2', 'KC_3', 'KC_4', 'KC_5', 'KC_6', 'KC_7', 'KC_8', 'KC_9', 'KC_0',
    'KC_ENTER', 'KC_ESCAPE', 'KC_BSPC', 'KC_TAB', 'KC_SPACE', 'KC_DELETE',
    'KC_MINUS', 'KC_EQUAL', 'KC_LEFT', 'KC_RIGHT', 'KC_UP', 'KC_DOWN',
    'KC_F1', 'KC_F2', 'KC_F3', 'KC_F4', 'KC_F5', 'KC_F6',
    'KC_F7', 'KC_F8', 'KC_F9', 'KC_F10', 'KC_F11', 'KC_F12',
    'KC_LEFT_GUI', 'KC_LEFT_SHIFT', 'KC_LEFT_CTRL', 'KC_LEFT_ALT',
]


class QmkView(Gtk.Box):
    def __init__(self, toast):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.toast = toast
        self.model = q.parse_keymap()
        self.layer = 0
        self.selected = None            # LAYOUT pos
        self.slot_edits = {}            # (layer, pos) -> slots dict
        self.hsv_edits = {}             # (layer, led) -> (h, s, v)
        self.hypr_label_edits = {}      # bind line_no -> new label
        self.header_label_edits = {}    # normalized keycode -> new label
        self.keys = {}                  # pos -> {button, cap, sub, swatch}
        self.layer_chips = {}
        self.hypr_index = {}            # (mods, key) -> hyprland label
        self.hypr_binds = {}            # (mods, key) -> bind
        self.assign_by_display = {}     # "Smith · Super+S" -> keycode
        self.assign_by_kc = {}          # normalized keycode -> display
        self.assign_displays = []
        self._block_drawer = False
        self._build()
        self.refresh()

    # ── current-value accessors ──────────────────────────────────────────────
    def disk_slots(self, pos):
        return q.decode_slots(self.model, self.model['layers'][self.layer][pos]['keycode'])

    def cur_slots(self, pos):
        if (self.layer, pos) in self.slot_edits:
            return dict(self.slot_edits[(self.layer, pos)])
        return self.disk_slots(pos)

    def cur_hsv(self, pos):
        led = q.LED_BY_LAYOUT[pos]
        if (self.layer, led) in self.hsv_edits:
            return self.hsv_edits[(self.layer, led)]
        leds = self.model['ledmap'][self.layer]
        return leds[led]['hsv'] if led < len(leds) else (0, 0, 0)

    def n_edits(self):
        return (len(self.slot_edits) + len(self.hsv_edits)
                + len(self.hypr_label_edits) + len(self.header_label_edits))

    # ── cross-reference / assignment options ─────────────────────────────────
    def set_hypr_index(self, binds, label_fn):
        self.hypr_index, self.hypr_binds = {}, {}
        self.assign_by_display, self.assign_by_kc = {}, {}
        displays = []
        for b in binds:
            key = (b['mods'], b['key'])
            lbl = label_fn(b)
            self.hypr_index[key] = lbl
            chord = q.hypr_to_chord(b['mods'], b['key'])
            if not chord:
                continue
            norm = q.chord_to_hypr(chord)
            if norm:
                self.hypr_binds[norm] = b
            combo = '+'.join(b['mods'] + (b['key'],)) if b['mods'] else b['key']
            disp = f'{lbl}  ·  {combo}'
            self.assign_by_display[disp] = chord
            self.assign_by_kc.setdefault(q._norm_kc(chord), disp)
            displays.append(disp)
        for kc in CURATED_KEYCODES:
            self.assign_by_display.setdefault(kc, kc)
            displays.append(kc)
        self.assign_displays = displays
        self._reload_completion()
        if self.selected is not None:
            self._load_drawer(self.selected)
        self.refresh()

    # ── build ────────────────────────────────────────────────────────────────
    def _build(self):
        chip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chip_row.set_halign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label='Layer:')
        lbl.get_style_context().add_class('muted')
        chip_row.pack_start(lbl, False, False, 0)
        for name, idx in LAYER_CHIPS:
            btn = Gtk.Button(label=name)
            btn.get_style_context().add_class('chip')
            if idx == 0:
                btn.get_style_context().add_class('active')
            btn.connect('clicked', self._on_layer, idx)
            self.layer_chips[idx] = btn
            chip_row.pack_start(btn, False, False, 0)
        self.pack_start(chip_row, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_halign(Gtk.Align.CENTER)
        self.pack_start(body, False, False, 0)

        kb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        kb.set_valign(Gtk.Align.START)
        kb.pack_start(self._build_half('LEFT', LEFT_MAIN, LEFT_THUMB), False, False, 0)
        kb.pack_start(self._build_half('RIGHT', RIGHT_MAIN, RIGHT_THUMB), False, False, 0)
        body.pack_start(kb, False, False, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.revealer.set_transition_duration(140)
        self.revealer.add(self._build_drawer())
        body.pack_start(self.revealer, False, False, 0)

    def _build_half(self, name, main, thumb):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hdr = Gtk.Label(label=f'— {name} —')
        hdr.get_style_context().add_class('half-label')
        box.pack_start(hdr, False, False, 0)
        grid = Gtk.Grid(row_spacing=4, column_spacing=4)
        for pos, r, c in main:
            grid.attach(self._make_key(pos), c, r, 1, 1)
        box.pack_start(grid, False, False, 0)
        tgrid = Gtk.Grid(row_spacing=4, column_spacing=4)
        tgrid.set_halign(Gtk.Align.CENTER)
        tgrid.set_margin_top(6)
        for pos, r, c in thumb:
            tgrid.attach(self._make_key(pos), c, r, 1, 1)
        box.pack_start(tgrid, False, False, 0)
        return box

    def _make_key(self, pos):
        btn = Gtk.Button()
        btn.get_style_context().add_class('qkey')
        btn.set_size_request(60, 54)
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cap = Gtk.Label()
        cap.get_style_context().add_class('qcap')
        cap.set_justify(Gtk.Justification.CENTER)
        cap.set_line_wrap(True)
        cap.set_max_width_chars(8)
        cap.set_ellipsize(Pango.EllipsizeMode.END)
        sub = Gtk.Label()
        sub.get_style_context().add_class('qbadge')
        sub.set_justify(Gtk.Justification.CENTER)
        sw = Gtk.DrawingArea()
        sw.set_size_request(-1, 6)
        sw.rgb = (0, 0, 0)
        sw.connect('draw', self._draw_swatch)
        vb.pack_start(cap, True, True, 0)
        vb.pack_start(sub, False, False, 0)
        vb.pack_start(sw, False, False, 0)
        btn.add(vb)
        btn.connect('clicked', self._on_key, pos)
        self.keys[pos] = {'button': btn, 'cap': cap, 'sub': sub, 'swatch': sw}
        return btn

    @staticmethod
    def _draw_swatch(area, cr):
        r, g, b = area.rgb
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        cr.set_source_rgb(r / 255, g / 255, b / 255)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        return False

    def _build_drawer(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class('drawer')
        box.set_size_request(290, -1)

        self.d_title = Gtk.Label(xalign=0)
        self.d_title.get_style_context().add_class('detail-key')
        box.pack_start(self.d_title, False, False, 0)

        box.pack_start(self._slot_header('Label'), False, False, 0)
        self.label_entry = Gtk.Entry()
        self.label_entry.get_style_context().add_class('label-entry')
        self.label_entry.set_placeholder_text('Name this key…')
        self.label_entry.connect('changed', self._on_label_changed)
        box.pack_start(self.label_entry, False, False, 0)
        self.label_hint = Gtk.Label(xalign=0)
        self.label_hint.get_style_context().add_class('xref')
        box.pack_start(self.label_hint, False, False, 0)

        box.pack_start(self._slot_header('Tap — hotkey or keycode'),
                       False, False, 0)
        self.tap_entry = self._assign_entry()
        self.tap_entry.connect('changed', self._on_slot_changed)
        box.pack_start(self.tap_entry, False, False, 0)
        self.xref = Gtk.Label(xalign=0)
        self.xref.get_style_context().add_class('xref')
        self.xref.set_line_wrap(True)
        self.xref.set_max_width_chars(36)
        box.pack_start(self.xref, False, False, 0)

        box.pack_start(self._slot_header('Double-tap — optional'), False, False, 0)
        self.dbl_entry = self._assign_entry()
        self.dbl_entry.set_placeholder_text('— none —')
        self.dbl_entry.connect('changed', self._on_slot_changed)
        box.pack_start(self.dbl_entry, False, False, 0)

        box.pack_start(self._slot_header('Hold — layer or modifier'), False, False, 0)
        self.hold_combo = Gtk.ComboBoxText()
        for label, _val in HOLD_OPTIONS:
            self.hold_combo.append_text(label)
        self.hold_combo.set_active(0)
        self.hold_combo.connect('changed', self._on_slot_changed)
        box.pack_start(self.hold_combo, False, False, 0)

        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                       False, False, 4)

        cl = Gtk.Label(label='Colour (HSV)', xalign=0)
        cl.get_style_context().add_class('muted')
        box.pack_start(cl, False, False, 0)
        self.big_swatch = Gtk.DrawingArea()
        self.big_swatch.set_size_request(-1, 28)
        self.big_swatch.rgb = (0, 0, 0)
        self.big_swatch.connect('draw', self._draw_swatch)
        box.pack_start(self.big_swatch, False, False, 0)

        self.sliders = {}
        for ch in ('H', 'S', 'V'):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            tag = Gtk.Label(label=ch)
            tag.get_style_context().add_class('muted')
            tag.set_size_request(14, -1)
            adj = Gtk.Adjustment(value=0, lower=0, upper=255, step_increment=1)
            sld = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
            sld.set_draw_value(True)
            sld.set_digits(0)
            sld.set_value_pos(Gtk.PositionType.RIGHT)
            sld.set_hexpand(True)
            sld.connect('value-changed', self._on_hsv_changed)
            self.sliders[ch] = sld
            row.pack_start(tag, False, False, 0)
            row.pack_start(sld, True, True, 0)
            box.pack_start(row, False, False, 0)

        sw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sw_row.set_homogeneous(True)
        for name, hsv in q.PALETTE:
            b = Gtk.Button()
            b.get_style_context().add_class('swatch-btn')
            b.set_size_request(-1, 18)
            b.set_tooltip_text(name)
            self._tint(b, hsv)
            b.connect('clicked', self._on_palette, hsv)
            sw_row.pack_start(b, True, True, 0)
        box.pack_start(sw_row, False, False, 0)

        return box

    def _slot_header(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class('muted')
        return lbl

    def _assign_entry(self):
        e = Gtk.Entry()
        e.get_style_context().add_class('label-entry')
        comp = Gtk.EntryCompletion()
        store = Gtk.ListStore(str)
        comp.set_model(store)
        comp.set_text_column(0)
        comp.set_match_func(self._match_anywhere, store)
        comp.connect('match-selected', self._on_match_selected, e)
        e.set_completion(comp)
        e._store = store
        return e

    @staticmethod
    def _match_anywhere(_comp, key, it, store):
        text = store[it][0].lower()
        return key.lower() in text

    def _on_match_selected(self, _comp, model, it, entry):
        entry.set_text(model[it][0])
        entry.set_position(-1)
        return True

    def _reload_completion(self):
        for entry in (getattr(self, 'tap_entry', None), getattr(self, 'dbl_entry', None)):
            if entry is None:
                continue
            store = entry._store
            store.clear()
            for disp in self.assign_displays:
                store.append([disp])

    def _tint(self, widget, hsv):
        prov = Gtk.CssProvider()
        prov.load_from_data(
            ('button { background-image:none; background-color:%s; '
             'border:1px solid rgba(0,0,0,0.25); box-shadow:none; }'
             % q.hsv_hex(*hsv)).encode())
        widget.get_style_context().add_provider(
            prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ── field <-> keycode helpers ────────────────────────────────────────────
    def _kc_to_field(self, kc):
        if not kc:
            return ''
        return self.assign_by_kc.get(q._norm_kc(kc), kc)

    def _field_to_kc(self, text):
        text = text.strip()
        if not text:
            return None
        return self.assign_by_display.get(text, text)

    # ── label resolution ─────────────────────────────────────────────────────
    def key_label(self, slots):
        tap = slots.get('tap')
        chord = q.chord_to_hypr(tap) if tap else None
        if chord and chord in self.hypr_binds:
            b = self.hypr_binds[chord]
            if b['line_no'] in self.hypr_label_edits:
                return self.hypr_label_edits[b['line_no']]
            return self.hypr_index.get(chord, display_label(b))
        if tap:
            nk = q._norm_kc(tap)
            if nk in self.header_label_edits:
                return self.header_label_edits[nk]
            meta = self.model['header'].get(nk)
            if meta and meta['label']:
                return meta['label']
        top, _ = q.cap_from_slots(self.model, slots)
        return top

    # ── refresh ──────────────────────────────────────────────────────────────
    def refresh(self):
        for pos, w in self.keys.items():
            slots = self.cur_slots(pos)
            hsv = self.cur_hsv(pos)
            ctx = w['button'].get_style_context()
            for cls in ('transparent', 'selected', 'unsaved'):
                ctx.remove_class(cls)
            top = self.key_label(slots)
            _t, sub = q.cap_from_slots(self.model, slots)
            if not top and not slots.get('hold') and not slots.get('double'):
                ctx.add_class('transparent')
            w['cap'].set_text(top)
            w['sub'].set_text(sub)
            w['swatch'].rgb = q.hsv_to_rgb(*hsv)
            w['swatch'].queue_draw()
            if pos == self.selected:
                ctx.add_class('selected')
            led = q.LED_BY_LAYOUT[pos]
            if (self.layer, pos) in self.slot_edits or \
               (self.layer, led) in self.hsv_edits or self._pos_label_edited(pos):
                ctx.add_class('unsaved')

    def _pos_label_edited(self, pos):
        slots = self.cur_slots(pos)
        tap = slots.get('tap')
        chord = q.chord_to_hypr(tap) if tap else None
        if chord and chord in self.hypr_binds:
            return self.hypr_binds[chord]['line_no'] in self.hypr_label_edits
        if tap:
            return q._norm_kc(tap) in self.header_label_edits
        return False

    # ── events ───────────────────────────────────────────────────────────────
    def _on_layer(self, _b, idx):
        self.layer = idx
        for i, b in self.layer_chips.items():
            ctx = b.get_style_context()
            (ctx.add_class if i == idx else ctx.remove_class)('active')
        if self.selected is not None:
            self._load_drawer(self.selected)
        self.refresh()

    def _on_key(self, _b, pos):
        self.selected = pos
        self._load_drawer(pos)
        self.revealer.set_reveal_child(True)
        self.refresh()

    def _load_drawer(self, pos):
        self._block_drawer = True
        slots = self.cur_slots(pos)
        self.d_title.set_text(self.key_label(slots) or f'Key {pos}')
        self.label_entry.set_text(self.key_label(slots))
        self.tap_entry.set_text(self._kc_to_field(slots.get('tap')))
        self.dbl_entry.set_text(self._kc_to_field(slots.get('double')))
        self.hold_combo.set_active(self._hold_index(slots.get('hold')))
        self._refresh_xref(slots.get('tap'))
        h, s, v = self.cur_hsv(pos)
        self.sliders['H'].set_value(h)
        self.sliders['S'].set_value(s)
        self.sliders['V'].set_value(v)
        self.big_swatch.rgb = q.hsv_to_rgb(h, s, v)
        self.big_swatch.queue_draw()
        self._block_drawer = False

    def _hold_index(self, hold):
        for i, (_label, val) in enumerate(HOLD_OPTIONS):
            if val == hold:
                return i
        return 0

    def _refresh_xref(self, tap):
        chord = q.chord_to_hypr(tap) if tap else None
        if not chord:
            self.xref.set_text('')
            self.label_hint.set_text('')
            return
        mods, key = chord
        combo = '+'.join(mods + (key,)) if mods else key
        if chord in self.hypr_binds:
            self.xref.set_text(f'↳ {combo} → {self.hypr_index.get(chord, "")}')
            self.label_hint.set_text('edits rename the Hyprland hotkey')
        else:
            self.xref.set_text(f'↳ sends {combo} (no Hyprland bind)')
            self.label_hint.set_text('label stored in keymap.c')

    def _read_slots(self):
        return {
            'tap': self._field_to_kc(self.tap_entry.get_text()),
            'double': self._field_to_kc(self.dbl_entry.get_text()),
            'hold': HOLD_OPTIONS[self.hold_combo.get_active()][1],
        }

    def _on_slot_changed(self, _w):
        if self._block_drawer or self.selected is None:
            return
        pos = self.selected
        slots = self._read_slots()
        if q.slots_equal(slots, self.disk_slots(pos)):
            self.slot_edits.pop((self.layer, pos), None)
        else:
            self.slot_edits[(self.layer, pos)] = slots
        self._refresh_xref(slots.get('tap'))
        self._block_drawer = True
        self.label_entry.set_text(self.key_label(slots))
        self._block_drawer = False
        self.refresh()
        self._notify_count()

    def _on_label_changed(self, entry):
        if self._block_drawer or self.selected is None:
            return
        slots = self.cur_slots(self.selected)
        tap = slots.get('tap')
        new = entry.get_text()
        chord = q.chord_to_hypr(tap) if tap else None
        if chord and chord in self.hypr_binds:
            b = self.hypr_binds[chord]
            ln, original = b['line_no'], display_label(b)
            if new == original or not new.strip():
                self.hypr_label_edits.pop(ln, None)
            else:
                self.hypr_label_edits[ln] = new
        elif tap:
            nk = q._norm_kc(tap)
            meta = self.model['header'].get(nk)
            original = meta['label'] if meta else ''
            if new == original or not new.strip():
                self.header_label_edits.pop(nk, None)
            else:
                self.header_label_edits[nk] = new
        self.refresh()
        self._notify_count()

    def _on_hsv_changed(self, _s):
        if self._block_drawer or self.selected is None:
            return
        pos = self.selected
        led = q.LED_BY_LAYOUT[pos]
        hsv = (int(self.sliders['H'].get_value()),
               int(self.sliders['S'].get_value()),
               int(self.sliders['V'].get_value()))
        disk_leds = self.model['ledmap'][self.layer]
        disk = disk_leds[led]['hsv'] if led < len(disk_leds) else (0, 0, 0)
        if hsv == disk:
            self.hsv_edits.pop((self.layer, led), None)
        else:
            self.hsv_edits[(self.layer, led)] = hsv
        self.big_swatch.rgb = q.hsv_to_rgb(*hsv)
        self.big_swatch.queue_draw()
        self.refresh()
        self._notify_count()

    def _on_palette(self, _b, hsv):
        if self.selected is None:
            return
        self._block_drawer = True
        self.sliders['H'].set_value(hsv[0])
        self.sliders['S'].set_value(hsv[1])
        self.sliders['V'].set_value(hsv[2])
        self._block_drawer = False
        self._on_hsv_changed(None)

    # ── save / flash gate ─────────────────────────────────────────────────────
    _count_cb = None
    _saved_cb = None

    def set_count_callback(self, cb):
        self._count_cb = cb
        self._notify_count()

    def set_saved_callback(self, cb):
        self._saved_cb = cb

    def _notify_count(self):
        if self._count_cb:
            self._count_cb(self.n_edits())

    def save(self, parent):
        if self.n_edits() == 0:
            return
        n = self.n_edits()
        try:
            q.write_full(self.model, self.slot_edits, self.hsv_edits,
                         self.header_label_edits)
        except Exception as e:
            self.toast(f'Write failed: {e}', error=True)
            return
        if self.hypr_label_edits:
            edits = [{'line_no': ln, 'new_label': lbl}
                     for ln, lbl in self.hypr_label_edits.items()]
            try:
                write_labels(edits)
                subprocess.Popen(['hyprctl', 'reload'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.Popen(['dots', f'relabel {len(edits)} hotkey(s)'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                self.toast(f'Hyprland relabel failed: {e}', error=True)
        flash.git_commit(f'moonlander: {n} key edit(s) via moonkeys')
        self.slot_edits.clear()
        self.hsv_edits.clear()
        self.hypr_label_edits.clear()
        self.header_label_edits.clear()
        self.model = q.parse_keymap()
        if self._saved_cb:
            self._saved_cb()
        self.refresh()
        self._notify_count()
        self.toast(f'Saved {n} edit(s), committed.')
        self._flash_gate(parent)

    def flash(self, parent):
        """Header Flash button: save pending edits first (that flow ends in the
        gate), otherwise flash what's already committed to keymap.c."""
        if self.n_edits() > 0:
            self.save(parent)
        else:
            self._flash_gate(parent)

    def _flash_gate(self, parent):
        dlg = Gtk.MessageDialog(
            transient_for=parent, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text='Build the firmware now?',
            secondary_text='Compile verifies the generated C. Flashing also needs '
                           'you at the board to push the reset pinhole when prompted.')
        dlg.add_button('Not yet', Gtk.ResponseType.CANCEL)
        dlg.add_button('Compile only', Gtk.ResponseType.NO)
        dlg.add_button('Compile + Flash', Gtk.ResponseType.YES)
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.YES:
            flash.compile_and_flash()
            self.toast('Flashing in terminal — push the reset pinhole.')
        elif resp == Gtk.ResponseType.NO:
            flash.compile_only()
            self.toast('Compiling in terminal.')
