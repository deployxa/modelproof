"""Human-readable HTML report (spec §12).

Security rules for report rendering:
  * EVERY dynamic string is escaped with html.escape — model output, error
    text, task ids, nothing is ever interpolated raw;
  * the report never embeds model-generated HTML as trusted markup;
  * private acceptance-test contents are never included — only check NAMES
    and first-line error summaries;
  * a fixture run renders a prominent, unmissable FIXTURE banner.
"""
from __future__ import annotations

import html
import json


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font: 15px/1.55 -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
       color: #1a1d23; background: #f6f7f9; margin: 0; padding: 2rem; }
.wrap { max-width: 1080px; margin: 0 auto; background: #fff; border: 1px solid #e3e6ea;
        border-radius: 10px; padding: 2rem 2.2rem; }
h1 { font-size: 1.45rem; margin: 0 0 .2rem; }
h2 { font-size: 1.05rem; margin: 1.8rem 0 .6rem; border-bottom: 2px solid #edf0f3; padding-bottom: .3rem; }
.mono, code, pre { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: .86em; }
.badge { display: inline-block; padding: .15rem .6rem; border-radius: 999px; font-weight: 600; font-size: .78rem;
         letter-spacing: .02em; margin-right: .4rem; }
.b-PASS { background: #dcfce7; color: #14532d; } .b-FAIL { background: #fee2e2; color: #7f1d1d; }
.b-INCOMPLETE { background: #fef3c7; color: #78350f; } .b-INVALID { background: #fee2e2; color: #7f1d1d; }
.b-INSUFFICIENT_EVIDENCE { background: #e2e8f0; color: #334155; }
.b-COMPLETED { background: #dcfce7; color: #14532d; } .b-FAILED { background: #fee2e2; color: #7f1d1d; }
.b-RUNNING { background: #dbeafe; color: #1e3a5f; } .b-QUEUED { background: #e2e8f0; color: #334155; }
.b-CANCELLED { background: #e2e8f0; color: #334155; }
.b-PASSED { background: #dcfce7; color: #14532d; } .b-TIMEOUT { background: #fef3c7; color: #78350f; }
.b-MODEL_ERROR { background: #fee2e2; color: #7f1d1d; } .b-SANDBOX_ERROR { background: #f3d9ff; color: #581c87; }
.b-INVALID_OUTPUT { background: #fef3c7; color: #78350f; }
.fixture-banner { background: #fff7ed; border: 2px solid #fb923c; color: #7c2d12; padding: .8rem 1rem;
                  border-radius: 8px; font-weight: 600; margin: 1rem 0; }
.explain { background: #f8fafc; border-left: 4px solid #94a3b8; padding: .7rem 1rem; margin: .8rem 0; }
table { width: 100%; border-collapse: collapse; margin: .6rem 0; font-size: .9rem; }
th, td { text-align: left; padding: .42rem .6rem; border-bottom: 1px solid #edf0f3; vertical-align: top; }
th { color: #64748b; font-weight: 600; background: #f8fafc; }
.kv td:first-child { color: #64748b; width: 280px; }
.note { color: #64748b; font-size: .85rem; }
.warnbox { background: #fef3c7; border: 1px solid #f59e0b33; border-radius: 8px; padding: .7rem 1rem; margin: .6rem 0; }
pre.snip { background: #f8fafc; border: 1px solid #e3e6ea; border-radius: 6px; padding: .5rem .7rem;
           overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
"""

_LIMITATIONS = """
<p class="note">Reading this report: the verdict certifies <b>execution and evidence
integrity</b> of this run only. It is not a model certification, not evidence that the
suite is independent of the model's training data, and not a claim of production
readiness. The Wilson interval describes sampling uncertainty under its assumptions; it
does not correct for task-selection bias, training contamination, prompt sensitivity, or
a non-representative benchmark. 24 tasks can expose obvious strengths and weaknesses and
verify infrastructure; they cannot support broad claims of model superiority.</p>
"""


def _esc(v) -> str:
    return html.escape(str(v if v is not None else "—"), quote=True)


def _badge(value: str) -> str:
    return f'<span class="badge b-{_esc(value)}">{_esc(value)}</span>'


def render_html(summary: dict, case_rows, gates, checksum_manifest: dict) -> str:
    is_fixture = bool(summary.get("is_fixture"))
    fixture_banner = (
        '<div class="fixture-banner">FIXTURE RUN — scripted outputs with replayed outcomes. '
        'No model ran and no code executed in a sandbox. This report demonstrates the pipeline '
        'and supports NO claim about any model.</div>'
        if is_fixture else ""
    )

    metrics_rows = [
        ("Task pass rate", _fmt_rate(summary.get("pass_rate"))),
        ("Pass-rate numerator / denominator",
         f"{_esc(summary.get('pass_rate_numerator'))} / {_esc(summary.get('pass_rate_denominator'))}"),
        ("Full-suite completion rate", _fmt_rate(summary.get("completion_rate"))),
        ("Wilson 95% CI", _fmt_ci(summary.get("wilson95"))),
        ("Passed / failed / timed out", f"{summary.get('passed')} / {summary.get('failed')} / {summary.get('timeouts')}"),
        ("Invalid outputs / model errors / sandbox errors",
         f"{summary.get('invalid_outputs')} / {summary.get('model_errors')} / {summary.get('sandbox_errors')}"),
    ]
    cat_rows = "".join(
        f"<tr><td>{_esc(cat)}</td><td>{_fmt_rate(rate)}</td></tr>"
        for cat, rate in (summary.get("category_pass_rates") or {}).items()
    )

    gate_rows = "".join(
        f"<tr><td>{_esc(g['gate'])}</td><td>{_badge(g['outcome'])}</td><td>{_esc(g['detail'])}</td></tr>"
        for g in gates
    )

    case_rows_html = "".join(
        "<tr>"
        f"<td class='mono'>{_esc(r['task_id'])}</td>"
        f"<td>{_esc(r['category'])}</td>"
        f"<td>{_badge(r['status'])}{' <span class=note>(resumed)</span>' if r['is_resumed'] else ''}</td>"
        f"<td>{_esc(r['checks_passed'])}/{_esc(r['checks_total'])}</td>"
        f"<td>{_esc(r['duration_ms']) if r['duration_ms'] is not None else '—'}</td>"
        f"<td>{_first_line(r['error_text'])}</td>"
        "</tr>"
        for r in case_rows
    )

    artifact_rows = "".join(
        f"<tr><td class='mono'>{_esc(rel)}</td><td class='mono'>{_esc(meta['sha256'][:24])}…</td>"
        f"<td>{_esc(meta['size'])}</td></tr>"
        for rel, meta in (checksum_manifest.get("artifacts") or {}).items()
    )

    raw_refs = ", ".join(sorted({f"raw/{r['task_id']}.txt" for r in case_rows})) or "—"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModelProof report — {_esc(summary.get('run_id'))}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>ModelProof evaluation report <span class="mono note">{_esc(summary.get('run_id'))}</span></h1>
<p>{_badge(summary.get('status'))} {_badge(summary.get('verdict'))}
   <span class="note">suite {_esc(summary.get('suite_id'))} v{_esc(summary.get('suite_version'))}
   · model {_esc(summary.get('model_name'))}</span></p>
{fixture_banner}
<div class="explain">{_esc(summary.get('verdict_explanation'))}</div>

<h2>Headline metrics</h2>
<table class="kv">{''.join(f'<tr><td>{k}</td><td class="mono">{v}</td></tr>' for k, v in metrics_rows)}</table>

<h2>Category pass rates</h2>
<table><tr><th>Category</th><th>Pass rate (valid outcomes)</th></tr>{cat_rows}</table>
<p class="note">Denominator excludes only SANDBOX_ERROR (infrastructure) cases; timeouts, invalid
outputs and model errors are model-attributable outcomes and are never dropped from the denominator.</p>

<h2>Integrity gates</h2>
<table><tr><th>Gate</th><th>Outcome</th><th>Detail</th></tr>{gate_rows}</table>

<h2>Per-case outcomes</h2>
<table>
<tr><th>Task</th><th>Category</th><th>Status</th><th>Checks</th><th>ms</th><th>First error (truncated)</th></tr>
{case_rows_html}
</table>
<p class="note">Raw model outputs are preserved as artifacts for independent inspection: {_esc(raw_refs)}.
Private acceptance-test contents are not reproduced here; only check names and truncated first errors.</p>

<h2>Provenance</h2>
<table class="kv">
<tr><td>Suite content hash (SHA-256)</td><td class="mono">{_esc(summary.get('suite_content_hash'))}</td></tr>
<tr><td>Run configuration hash</td><td class="mono">{_esc(summary.get('config_hash'))}</td></tr>
<tr><td>Prompt template hash</td><td class="mono">{_esc(summary.get('prompt_template_hash'))}</td></tr>
<tr><td>Suite provenance status</td><td>{_badge(summary.get('provenance_status'))}</td></tr>
<tr><td>Leakage scan</td><td>{_esc(summary.get('leakage'))}</td></tr>
<tr><td>Environment</td><td class="mono">{_esc(_env(summary.get('environment')))}</td></tr>
<tr><td>Started / ended</td><td class="mono">{_esc(summary.get('started_at'))} → {_esc(summary.get('ended_at'))}</td></tr>
</table>
<div class="warnbox">A hash establishes artifact <b>identity</b>, not correctness, authorship, or
independence from training data. Suite independence is <b>INDEPENDENCE_UNVERIFIED</b> unless a
documented contamination audit says otherwise.</div>

<h2>Artifact checksums (SHA-256)</h2>
<table><tr><th>Artifact</th><th>SHA-256 (prefix)</th><th>Bytes</th></tr>{artifact_rows}</table>
<p class="note">Recompute independently: <code>modelproof run verify {_esc(summary.get('run_id'))}</code>
or <code>sha256sum</code> over the artifact directory against <code>checksums.json</code>.</p>

<h2>Limitations</h2>
{_LIMITATIONS}
</div></body></html>"""


def _fmt_rate(v) -> str:
    return "—" if v is None else f"{v:.4f} ({v * 100:.1f}%)"


def _fmt_ci(v) -> str:
    return "no estimate (no valid outcomes)" if v is None else f"[{v[0]:.4f}, {v[1]:.4f}]"


def _first_line(text) -> str:
    if not text:
        return "—"
    line = str(text).splitlines()[0]
    return html.escape(line[:160], quote=True)


def _env(env) -> str:
    if not env:
        return "—"
    return ", ".join(f"{k}={v}" for k, v in sorted(env.items()))
