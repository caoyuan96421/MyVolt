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
  provides Emergency Stop with `M18`.
- The compact Stage Control panel jogs X/Y with `V2`, jogs Z and extrusion with
  `V1`, and probes the current location with `V4 R1` or `V4 R0.1`.
- The Temperature panel sits below Stage Control and sets heater temperature
  using `M141 T... D...`; duration is capped at `3600 s`.
- Stop heating with `M142`.
- Alignment, Leveling, and Printing sit on the right side of the stage view.
- The Alignment panel shows two UVC webcam previews with independent camera
  selectors and a shared exposure-compensation slider. Selections and exposure
  are stored in `camera.json` and restored on startup when those cameras are
  available. Exposure control is enabled only for active cameras that report Qt
  `ExposureCompensation` support.
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
calibration area.

Drag inside the rectangle to move it. Drag an edge or corner to resize it. A
plain click still moves the tool if motion is enabled.

Choose X/Y probe counts in the Leveling panel and start probing. The GUI probes
an evenly spaced grid over the rectangle:

- First point: `V4 R1` for coarse positioning, then `V4 R0.1`; only the `R0.1`
  result is stored.
- Remaining points: `V4 R0.1` only.
- Height-map values are stored from returned `probeMeasurement z`.

Collected points are displayed live as colored dots inside the work rectangle.
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
