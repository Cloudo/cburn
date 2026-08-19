// The spend instrument. The scale is compressed: the burn rate of a live machine roams from
// thousands to tens of millions of tokens per minute, and on a linear scale the needle
// either lies at zero or hits the stop.
//
// The ring under the scale is split into shares of the parts: cache reads are usually two
// orders of magnitude larger than the rest, and without the split the instrument would show
// one colour and nothing else.

import { useState, type MouseEvent } from "react";

import { compact, grouped } from "./format";
import { useLang } from "./i18n";

export type Slice = { key: string; label: string; value: number; color: string };

// The scale is a gentle power rather than a plain logarithm. On a logarithm a five sits at
// 70% of its decade instead of in the middle, so 5 M pressed against 10 M - and the top of
// the arc is exactly where a working machine keeps the needle. The exponent is low enough
// to hold four orders of magnitude on one semicircle and high enough to open the top up.
const MIN = 1_000;
const MAX = 10_000_000;
const CURVE = 0.15;

// The decades carry the numbers, and 5 M stands among them: between a million and ten the
// needle would otherwise be read by guessing.
const MARKS = [1e3, 1e4, 1e5, 1e6, 5e6, 1e7];

const CX = 200;
const CY = 190;
const R_SCALE = 148;
const R_RING_OUTER = 176;
const R_RING_INNER = 168;

function polar(radius: number, fraction: number) {
  const angle = Math.PI * (1 + fraction); // 0 is on the left, 1 on the right
  return { x: CX + radius * Math.cos(angle), y: CY + radius * Math.sin(angle) };
}

function arc(radius: number, from: number, to: number): string {
  const start = polar(radius, from);
  const end = polar(radius, to);
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 0 1 ${end.x} ${end.y}`;
}

function ringSegment(from: number, to: number): string {
  const outerStart = polar(R_RING_OUTER, from);
  const outerEnd = polar(R_RING_OUTER, to);
  const innerEnd = polar(R_RING_INNER, to);
  const innerStart = polar(R_RING_INNER, from);
  return [
    `M ${outerStart.x} ${outerStart.y}`,
    `A ${R_RING_OUTER} ${R_RING_OUTER} 0 0 1 ${outerEnd.x} ${outerEnd.y}`,
    `L ${innerEnd.x} ${innerEnd.y}`,
    `A ${R_RING_INNER} ${R_RING_INNER} 0 0 0 ${innerStart.x} ${innerStart.y}`,
    "Z",
  ].join(" ");
}

/** The position of a value on the scale, 0...1. */
export function scalePosition(value: number): number {
  if (value <= MIN) return 0;
  if (value >= MAX) return 1;
  return (value ** CURVE - MIN ** CURVE) / (MAX ** CURVE - MIN ** CURVE);
}

// every part that exists at all keeps at least three degrees of the semicircle
const RING_FLOOR = 3 / 180;

/** Arc widths of the ring, 0...1 summing to one.
 *
 * Honest shares paint the whole ring one colour: cache reads outrun the rest by two
 * orders of magnitude. So the widths are compressed - proportional to square roots,
 * which turns "200 times more" into "14 times more" - and every nonzero part is
 * guaranteed a floor sliver. The honest percentages stay in the legend and the
 * tooltips; the ring is a map of the colours, not the diagram of the shares.
 */
export function ringShares(values: number[]): number[] {
  const roots = values.map((value) => Math.sqrt(Math.max(value, 0)));
  const total = roots.reduce((sum, root) => sum + root, 0);
  if (total <= 0) return values.map(() => 0);
  const alive = roots.filter((root) => root > 0).length;
  // the floor is handed out first, the rest of the arc is split by the roots
  const budget = 1 - RING_FLOOR * alive;
  return roots.map((root) => (root > 0 ? RING_FLOOR + (root / total) * budget : 0));
}

type Props = { value: number; slices: Slice[]; caption: string };

export function Gauge({ value, slices, caption }: Props) {
  const { t } = useLang();
  const position = scalePosition(value);
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);

  const arcs = ringShares(slices.map((slice) => slice.value));
  let cursor = 0;
  const segments = slices.map((slice, index) => {
    // the tooltip keeps the honest share; the arc width is compressed (see ringShares)
    const share = total > 0 ? slice.value / total : 0;
    const segment = { ...slice, from: cursor, to: cursor + arcs[index], share };
    cursor += arcs[index];
    return segment;
  });

  return (
    <figure className="gauge">
      <svg viewBox="0 0 400 232" role="img" aria-label={`${caption}: ${compact(value)}`}>
        <path className="gauge-track" d={arc(R_SCALE, 0, 1)} />

        {total > 0 &&
          segments.map((segment) => (
            <path
              key={segment.key}
              className="gauge-slice"
              d={ringSegment(segment.from, segment.to)}
              fill={segment.color}
            >
              {/* a sliver lifted by the floor would otherwise introduce itself as 0% */}
              <title>
                {`${segment.label}: ${
                  segment.share * 100 < 1 ? "<1" : Math.round(segment.share * 100)
                }%`}
              </title>
            </path>
          ))}

        {MARKS.map((mark, index) => {
          const fraction = scalePosition(mark);
          const outer = polar(R_SCALE, fraction);
          const inner = polar(R_SCALE - 14, fraction);
          // The outermost labels press against the arc ends, so we push them deeper.
          const edge = index === 0 || index === MARKS.length - 1;
          const label = polar(R_SCALE - (edge ? 52 : 32), fraction);
          return (
            <g key={mark} className="gauge-tick">
              <line x1={outer.x} y1={outer.y} x2={inner.x} y2={inner.y} />
              <text x={label.x} y={label.y} dominantBaseline="middle" textAnchor="middle">
                {compact(mark)}
              </text>
            </g>
          );
        })}

        {/* The needle is drawn to the left (the zero position) and rotated: the CSS
            transition works with transform, and it does not animate the x1/y1/x2/y2
            attributes of a line - the needle jumped because of them. The hub is put at the
            local origin by the group, so that the pivot is a bare zero: a transform-origin
            in pixels is multiplied by the interface zoom in WebKit, and at 1.25 the needle
            went rotating around a point outside the instrument. */}
        <g transform={`translate(${CX} ${CY})`}>
          <line
            className="gauge-needle"
            x1={16}
            y1={0}
            x2={-(R_SCALE - 18)}
            y2={0}
            style={{ transform: `rotate(${position * 180}deg)` }}
          />
        </g>
        <circle className="gauge-hub" cx={CX} cy={CY} r={9} />
      </svg>

      <figcaption>
        {/* Read like a speedometer: the figure holds a field of its own width, so it does
            not jump about as it grows from a nought to millions, and the unit stands under
            it, smaller and quieter. The window used to be written here as a third line -
            the picker in the header names it already. */}
        <strong className="gauge-value">{compact(value)}</strong>
        <span className="gauge-rate">{t("gauge.rate")}</span>
      </figcaption>
    </figure>
  );
}

/** A linear scale of output tokens: there are few of them, and a logarithm only hinders. */
export function OutputMeter({ value, peak }: { value: number; peak: number }) {
  const { t } = useLang();
  const ceiling = Math.max(peak, 1000);
  const share = Math.min(value / ceiling, 1);
  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-label">{t("meter.label")}</span>
        <span className="meter-value">
          {compact(value)} <span className="meter-unit">{t("meter.unit")}</span>
        </span>
      </div>
      <div className="meter-track">
        <div className="meter-fill" style={{ width: `${share * 100}%` }} />
      </div>
      <div className="meter-scale">
        <span>0</span>
        <span>{compact(ceiling / 2)}</span>
        <span>{compact(ceiling)}</span>
      </div>
    </div>
  );
}

/** The chart recorder: spend by time buckets. The step is set by the server (5 seconds now). */
export function Recorder({
  series,
  bucketSeconds,
}: {
  series: Array<{ at: string; tokens: number; output_tokens: number; turns: number }>;
  bucketSeconds: number;
}) {
  // The height follows the model output: in total tokens every turn looks
  // the same, because cache reads outweigh everything else.
  const { t } = useLang();
  const peak = Math.max(...series.map((bucket) => bucket.output_tokens), 1);
  const span = Math.round((series.length * bucketSeconds) / 60);
  const [hover, setHover] = useState<number | null>(null);

  // The bucket is found by the cursor position over the whole track rather than by
  // hovering the bar itself: empty one-pixel buckets could not be caught otherwise.
  const track = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const index = Math.floor(((event.clientX - box.left) / box.width) * series.length);
    setHover(Math.min(Math.max(index, 0), series.length - 1));
  };

  const active = hover === null ? null : series[hover];

  return (
    <div className="recorder">
      <div className="recorder-head">
        <span className="recorder-label">{t("recorder.label", { seconds: bucketSeconds })}</span>
        <span className="recorder-span">{t("recorder.span", { minutes: span })}</span>
      </div>
      <div
        className="recorder-track"
        onMouseMove={track}
        onMouseLeave={() => setHover(null)}
      >
        {series.map((bucket, index) => (
          <span
            key={bucket.at}
            className={[
              "recorder-bar",
              bucket.turns > 0 ? "recorder-bar-on" : "",
              index === hover ? "recorder-bar-hover" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            style={{
              height: `${Math.max((bucket.output_tokens / peak) * 100, bucket.turns ? 6 : 1)}%`,
            }}
          />
        ))}

        {active && (
          // The tooltip stands at the track edge opposite the cursor: following it,
          // it would crawl out of the panel and twitch on every move.
          <div
            className={
              hover! < series.length / 2 ? "recorder-tip recorder-tip-right" : "recorder-tip"
            }
          >
            <span className="recorder-tip-time">{bucketClock(active.at, bucketSeconds)}</span>
            <span className="recorder-tip-row">
              {t("recorder.output")} <strong>{grouped(active.output_tokens)}</strong>
            </span>
            <span className="recorder-tip-row">
              {t("recorder.total")} <strong>{compact(active.tokens)}</strong>
            </span>
            <span className="recorder-tip-row">
              {t("recorder.turns")} <strong>{active.turns}</strong>
            </span>
          </div>
        )}
      </div>
      <div className="recorder-scale">
        <span>{t("recorder.ago", { minutes: span })}</span>
        <span>{t("recorder.peak", { value: compact(peak) })}</span>
        <span>{t("recorder.now")}</span>
      </div>
    </div>
  );
}

/** The bucket caption: the interval it covers. */
function bucketClock(at: string, seconds: number): string {
  const from = new Date(at);
  const to = new Date(from.getTime() + seconds * 1000);
  const clock = (date: Date) =>
    date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${clock(from)} — ${clock(to)}`;
}
