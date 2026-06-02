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

When all axes report homed, the status bar shows `HOMED` in green. If any axis
is not homed, it shows the unhomed axes in red. Manual motion controls are
disabled unless all axes are homed.

The GUI follows a conservative synchronization policy: manual motion commands
are followed by `M400`, and new non-emergency commands are blocked while waiting
for motion synchronization. `M18` remains available as Emergency Stop.

## Controls

- Connect/disconnect to the selected COM port.
- Home XY with `V5`.
- Prepare the installed tool with `V3 Z`.
- Emergency stop with `M18`.
- Set heater temperature using `M141 T... D...`; duration is capped at `3600 s`.
- Stop heating with `M142`.
- Jog X/Y with `V2`, jog Z and extrusion with `V1`.
- Probe at the current location with `V4 R1` or `V4 R0.1`.
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

Use Delete Height Map to clear the recorded points.
