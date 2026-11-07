# Command line and terminal wizard

`mcap-editor` is the whole tool. Without a command (or with only a path) it starts the browser UI, like
`mcap-editor-ui`. `mcap-editor -h` lists all commands, `mcap-editor <command> -h` their options.

Times are seconds after the first message of the recording; an end beyond the recording means "until the end".
Inputs are never changed and outputs are never overwritten.

## Commands

| Command | What it does |
|---|---|
| `mcap-editor ui [path] [--port P] [--no-browser]` | browser UI (same as `mcap-editor-ui`) |
| `mcap-editor tui <in.mcap>` | interactive wizard in the terminal |
| `mcap-editor info <in.mcap>` | topics, message counts, rates, sizes, duration |
| `mcap-editor cut <in.mcap> [options]` | write an edited copy |
| `mcap-editor init <in.mcap> [-o job.yaml]` | write a job file listing all topics, to edit by hand |
| `mcap-editor run <job.yaml>` | execute a job file |

## Terminal wizard (`tui`)

Shows the topics with message counts, rates and sizes. Tick the topics to keep (space toggles, `i` inverts, `a` all),
type the start and end, confirm the output name and the write. The choices are saved next to the output as
`<output>.job.yaml`, so the same edit can be repeated with `mcap-editor run`.

## `cut` options

| Option | Meaning |
|---|---|
| `-o OUT` | output file (default `<in>_edit.mcap` next to the input) |
| `--keep T …` / `--drop T …` | keep only these topics / drop these topics |
| `--start S`, `--end S` | time window in s after the first message |
| `--no-latched` | do not include latched messages from before the start |
| `--latched-off T …` | … except for these topics |
| `--latched T …` | treat these topics as latched as well (files without QoS information) |
| `--compression keep\|zstd\|lz4\|none` | chunk compression of the output (default: as the input) |
| `--split-duration S`, `--split-size GB` | split into `<out>_0.mcap`, `_1` … |
| `--latched-first-part-only` | when splitting, latched messages only in part 0 (default: every part) |
| `--save-job YAML` | also save the settings as a job file |

Examples:

```bash
mcap-editor cut rec.mcap --keep /cam /tf_static --start 2 --end 20
mcap-editor cut rec.mcap --drop /imu --split-duration 60 --compression lz4 -o parts.mcap
```

## Job files

A job file holds one edit (input, output, topics, window, options) as YAML, so it can be repeated or applied to
another recording:

```bash
mcap-editor init rec.mcap -o job.yaml     # all topics listed with true; set false to drop, set start / end
mcap-editor run job.yaml
```

The browser UI and the wizard save a job file next to every output.

## Latched topics

Topics published as latched (ROS 2 `transient_local`, e.g. `/tf_static`, `/robot_description`; ROS 1 `latching`)
are often sent only once at the start of a recording. When a cut starts later, their last messages from before the
start are included with the start as their time, so the cut still has them: as many as the publisher's QoS depth,
all if unknown. Only for selected topics.

## Settings

Defaults ship in `src/mcap_editor_ui/core/config/defaults.yaml`; your changes go to
`~/.config/mcap_editor_ui/settings.yaml` (only values that differ). Edit them in the browser UI (gear icon) or in
that file: accent colour, theme, size unit, output name suffix, latched / compression / split defaults, undo steps,
port. The command line uses them as defaults; its options override them.
