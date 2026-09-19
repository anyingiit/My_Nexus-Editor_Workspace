<!-- Source: Best-README-Template BLANK_README (Unlicense) — https://github.com/othneildrew/Best-README-Template -->
<a id="readme-top"></a>

# My_Nexus-Editor_Workspace

A personal workspace archiving an abandoned attempt to build and evaluate a Nexus-Editor contribution AGENTS.md with AI coding agents, holding a My_Nexus-Editor_Workspace_V1 snapshot and its nexus-eval harness alongside an unresolved Nexus-Editor git submodule, with no buildable source of its own.

**English** · [简体中文](README.zh-CN.md)

[![License](https://img.shields.io/github/license/anyingiit/My_Nexus-Editor_Workspace)](LICENSE)

[Report a bug](https://github.com/anyingiit/My_Nexus-Editor_Workspace/issues/new?template=bug_report.yml) · [Request a feature](https://github.com/anyingiit/My_Nexus-Editor_Workspace/issues/new?template=feature_request.yml)

<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#about-the-project">About The Project</a></li>
    <li><a href="#getting-started">Getting Started</a></li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
  </ol>
</details>

## About The Project

`My_Nexus-Editor_Workspace` is anyingiit's personal archive of one attempt to write and evaluate a layered, contribution-only `AGENTS.md` for the [Nexus-Editor](https://github.com/floatboatai/Nexus-Editor) editor project using AI coding agents configured under `.opencode/agent/`. That attempt lives entirely under `My_Nexus-Editor_Workspace_V1/`, whose own `README.md` records the run as abandoned, and its `nexus-eval/` subdirectory holds the dataset, rubric, and reports the attempt produced while scoring itself. At the repository root, `Nexus-Editor` is declared only as a git submodule pointing at that separate, third-party project (see [.gitmodules](.gitmodules)) and is left unresolved in this checkout, so there is no source of this repository's own to compile or run.

## Getting Started

### Prerequisites

- A text editor or Markdown/JSON viewer, to read the archived notes and datasets — everything here is plain text.
- Optionally, Git with submodule support, only if you want to fetch the code the top-level `Nexus-Editor` submodule points at; see [.gitmodules](.gitmodules). That fetches a separate repository and is not needed to read anything else in this workspace.

### Installation

There is no build step and nothing of this repository's own to install. Cloning it gets you the archived files:

```sh
git clone https://github.com/anyingiit/My_Nexus-Editor_Workspace.git
cd My_Nexus-Editor_Workspace
```

The top-level `Nexus-Editor/` directory stays empty unless you separately run `git submodule update --init`, which fetches `https://github.com/floatboatai/Nexus-Editor` — a different repository — and is entirely optional.

## Usage

There is nothing to run; the point of this workspace is to read what it recorded:

```sh
$EDITOR My_Nexus-Editor_Workspace_V1/README.md            # the attempt's own status note
$EDITOR My_Nexus-Editor_Workspace_V1/nexus-eval/README.md  # what the evaluation harness built and measured
```

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for how to open an issue or a pull request, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the standards expected of everyone taking part.

Please do not report security issues in public issues or pull requests. [SECURITY.md](SECURITY.md) explains how to report them privately.

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.

## Contact

Project link: [https://github.com/anyingiit/My_Nexus-Editor_Workspace](https://github.com/anyingiit/My_Nexus-Editor_Workspace)

<p align="right">(<a href="#readme-top">back to top</a>)</p>
