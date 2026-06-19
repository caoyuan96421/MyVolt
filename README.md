# MyVolt Controller

PySide6 control GUI for a Voltera-like solder paste dispenser using the
reverse-engineered serial protocol documented in `syntax.md`.

## Setup

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the GUI:

```powershell
python main.py
```

The controller serial link uses fixed `250000` baud and line-feed-only
termination. The port selector lists available COM ports; raw commands and
controller responses are shown in the log window.

## GUI Behavior

The status bar updates only from asynchronous controller messages, without
polling:

- `positionUpdate` updates X, Y, Z, and E.
- `bedTemperatureUpdate` updates current and target bed temperature.
- `homedStatusUpdate` updates homing state.
- `toolUpdate` updates the detected tool/dispenser type.
- `probeMeasurement` updates the latest probe Z reading.
- `log: setting maximum Z position to ...` updates the current tool's maximum
  Z height for safe retract/return moves.

When all axes report homed, the status bar shows `HOMED` in green. If any axis
is not homed, it shows the unhomed axes in red. Manual motion controls are
disabled unless all axes are homed.

The GUI follows a conservative synchronization policy: manual motion commands
are followed by `M400`, and new non-emergency commands are blocked while waiting
for motion synchronization. `M18` remains available as Emergency Stop.

## Controls

- The Hardware panel connects/disconnects the selected COM port, refreshes the
  port list, homes XY with `V5`, prepares the installed tool with `V3 Z`, and
  provides Emergency Stop with `M18`. The selected COM port is stored in
  `conn.json` and restored on startup when available. Stay alive stores its
  setting in the same file and sends an idle `M400` query every 60 seconds while
  connected to avoid the controller's idle shutoff.
- The compact Stage Control panel jogs X/Y with `V2`, jogs Z and extrusion with
  `V1`, and probes the current location with `V4 R1` or `V4 R0.1`.
- The Temperature panel sits below Stage Control and sets heater temperature
  using `M141 T... D...`; duration is capped at `3600 s`.
- Stop heating with `M142`.
- The left column contains Hardware, Stage Control, Temperature, and Raw Log.
  The center column contains Stage View, Alignment, and Raw Payload. Pattern,
  Leveling, and Printing sit on the right side of the stage view.
- The Alignment panel shows two side-by-side UVC webcam previews with
  independent camera selectors and a shared exposure-compensation slider.
  Selections, view rotations, and exposure are stored in `camera.json` and
  restored on startup when those cameras are available. Exposure control is
  enabled only for active cameras that report Qt `ExposureCompensation` support.
  Scroll over a camera preview to zoom that view.
- The Pattern panel loads point patterns, overlays transformed dots on the stage
  view, assists two-point camera alignment, and prints each pattern point as a
  paste dot. `dummy_pattern.csv` is included as a simple 20 x 20 mm test pattern.
- Print a height-map-compensated circle from the Printing panel.
- Send unframed raw payloads; the app frames them as `N... *crc,length`.

Probing is allowed only when the controller-reported tool type is `Probe`.
Prepare Tool is allowed for all detected tool types.

## Tool Offsets

Probe and dispenser offsets are displayed in the Leveling panel. They begin as
`Undefined` after connection. Installing a tool clears that tool's displayed
offset until preparation completes.

During preparation, the controller may emit multiple
`Measure at switch: z-switch (z-min)` measurements. The GUI keeps only the last
measurement before `Preparing tool -- completed ...` and displays it as the
offset for the current tool. These values are displayed for operator awareness;
the controller appears to handle actual tool-offset compensation internally.

## Stage View

The stage view shows the XY stage boundaries:

- X range: `0..128 mm`.
- Y range: `0..157 mm`.
- The top `40 mm` is shaded as calibration-only area.
- Positive X is toward the left.
- Positive Y is toward the bottom.

A cross shows the current tool/probe position from `positionUpdate`. If the axes
are not homed, the cross is gray. Left-clicking the stage moves to that XY
position when motion is enabled. The cursor is a gray cross when motion is
allowed and a disabled icon when motion is blocked because axes are not homed.
Scroll over the stage view to zoom in and out around the cursor position.

The stage view marks:

- Z probe region: center `X4.820494 Y7.966725`, diameter `8 mm`.
- XY probe region: center `X34.270011 Y5.686648`, diameter `15 mm`.

## Height Map

The Leveling panel controls height-map probing. The work area is an editable
rectangle in the stage view, not a numeric form. It defaults to a `50 x 50 mm`
square centered in the user stage region and cannot extend into the top
calibration area. The work area and X/Y probe counts persist in `leveling.json`.

Drag an edge or corner to resize the rectangle. Dragging inside the rectangle is
disabled; a plain click inside it still moves the tool if motion is enabled.

Choose X/Y probe counts in the Leveling panel and start probing. The GUI probes
an evenly spaced grid over the rectangle:

- First point: `V4 R1` for coarse positioning, then `V4 R0.1`; only the `R0.1`
  result is stored.
- Remaining points: `V4 R0.1` only.
- Height-map values are stored from returned `probeMeasurement z`.

Planned probe locations are shown before probing as small hollow dots. Collected
points are displayed live as colored dots inside the work rectangle.

While probing, Start Probing changes to Abort. Abort finishes the current
in-flight probe, keeps any points already measured, then runs the normal probe
cleanup and max-Z/z-switch return sequence.

The stage view shows a matching height colorbar to the right of the stage, using
the current collected min/max Z range.

When height-map probing finishes, the app disables probe safety, raises to
`reported maximum Z - 0.5 mm`, moves back to the z-switch XY position, then
synchronizes with `M400`. If the maximum Z has not been reported yet, the app
uses `V3 Z` as the fallback retract/return command.

Use Save to write the height map as JSON, including the probed points and current
work-area rectangle. Use Load to restore a saved height map. Use Delete Height
Map to clear the recorded points.

## Printing

The Printing panel currently supports a circle print primitive. It exposes:

- Print speed in `mm/s`, default `200`.
- Print height in `mm`, default `0.15`; this is sent through `V102`.
- Kick in `um`, default `200`; this is converted to a positive `E` move.
- Retract in `um`, default `200`; this is converted to a negative `E` move.
- Max length in `mm`, default `30`; longer circles are split into multiple
  primed/retracted trace chunks.
- Travel height in `mm`, default `2`; this is also handled through `V102`.

Press Print Circle to enter circle-edit mode. The work-area rectangle is locked
while editing the circle. Drag inside the circle to move it, or drag the circle
edge to resize it. The button changes to Start; pressing Start queues the print.

Printing requires a completed or loaded height map. The Print Circle workflow
can be entered even if the status bar is not currently `HOMED`; pressing Start
first queues `V3 Z` and `M400` so the installed tool is prepared automatically
and the controller can report the current maximum Z. Each compensated
`V1 ... D` command uses an interpolated height-map Z value directly; the app
does not add print height to Z because the `V102` hover height provides that
offset.

After preparation, the app clears `V102` and moves XY directly to the print
start from the prepared z-switch/max-Z state. After the print completes, it
clears `V102`, raises to `reported maximum Z - 0.5 mm`, moves back to the
z-switch XY position, synchronizes with `M400`, and hides the edited circle from
the stage view.

## Pattern Alignment

A pattern is a list of PCS `x,y` points. CSV files should contain either a
header row followed by `x,y` values or raw comma-separated `x,y` rows. The app
also accepts JSON lists or objects with a `points` list.

Loaded pattern dots are transformed into stage WCS by flipping both PCS axes,
scaling, rotating by the current angle, then applying the displayed work offset.
Loading a pattern does not change the current alignment transform or red-cross
alignment points. At startup and after Reset, the transform uses the bottom-left
of the visible stage view with zero rotation and `1.0` scale.

Click & Align opens an Alignment Procedure dialog for the two-point workflow:
select a pattern dot, move the machine to the matching physical point with Z
raised, lower to the adjustable Align Z height in the dialog, fine-align using
the cameras, and confirm. The second point repeats the process and updates work
offset, rotation, and scale. While selecting a pattern dot, the hovered point is
highlighted and the selected point keeps a colored perimeter.

Align to Anchors uses the same dialog, but the two PCS anchor coordinates are
entered directly with X/Y spin boxes instead of selecting dots on the stage view.
It can be run without a loaded pattern as long as the stage is enabled. The
dialog shows the current workflow step and includes XY jog buttons wired to
coarse and fine XY step settings. Coarse defaults to `1 mm`; fine defaults to
`0.05 mm`. Rough/go-to workflow steps use the coarse step; fine-align steps use
the fine step. Typed anchor positions are stored in `anchors.json` and restored
as the next dialog defaults. The dialog Align Z and XY step settings are stored
in the same file. If the measured two-point scale differs from nominal by more than
`2%`, the app shows a warning.

The pattern points used for alignment are shown on the stage as red crosses.
The stage view also draws green PCS X/Y arrows using the current alignment
transform, so the arrows include the active flips, offset, rotation, and scale.
After alignment completes, the leveling work area is automatically resized to the
bounding box of the transformed dot pattern plus those alignment points, with a
default `2 mm` margin. Completing or canceling an alignment queues a retract to
`reported maximum Z - 0.5 mm`; if maximum Z is not known yet, the app falls back
to `V3 Z`.

Use Save Alignment and Load Alignment to store or restore the current alignment
as JSON. Alignment files contain the work offset, rotation, scale, and the PCS
alignment points shown as red crosses. Saving and loading alignment does not
require a pattern to be loaded and does not replace the loaded pattern.

Pattern dot printing uses the Pattern panel print height, kick, retract, and
travel height settings. It requires a height map and uses the same height-map Z
interpolation as circle printing. During dot printing, the current dot is yellow
and completed dots are green. Starting a new pattern print resets all dots to the
unprinted color first.
