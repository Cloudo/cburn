// The spend instrument. The scale is logarithmic: the burn rate of a live machine roams
// from thousands to tens of millions of tokens per minute, and on a linear scale the
// needle either lies at zero or hits the stop.
//
// The ring under the scale is split into shares of the parts: cache reads are usually two
// orders of magnitude larger than the rest, and without the split the instrument would

import { useState, type MouseEvent } from "react";

import { compact, grouped } from "./format";
import { useLang } from "./i18n";

export type Slice = { key: string; label: string; value: number; color: string };

const DECADES = [3, 4, 5, 6, 7]; // 1k ... 10M tokens per minute
const MIN = 10 ** DECADES[0];
const MAX = 10 ** DECADES[DECADES.length - 1];

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

/** The position of a value on the logarithmic scale, 0...1. */
export function scalePosition(value: number): number {
  if (value <= MIN) return 0;
  if (value >= MAX) return 1;
  const span = Math.log10(MAX) - Math.log10(MIN);
  return (Math.log10(value) - Math.log10(MIN)) / span;
}

type Props = { value: number; slices: Slice[]; caption: string };

export function Gauge({ value, slices, caption }: Props) {
  const { t } = useLang();
  const position = scalePosition(value);
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);

  let cursor = 0;
  const segments = slices.map((slice) => {
    const share = total > 0 ? slice.value / total : 0;
    const segment = { ...slice, from: cursor, to: cursor + share, share };
    cursor += share;
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
              <title>{`${segment.label}: ${Math.round(segment.share * 100)}%`}</title>
            </path>
          ))}

        {DECADES.map((decade, index) => {
          const fraction = index / (DECADES.length - 1);
          const outer = polar(R_SCALE, fraction);
          const inner = polar(R_SCALE - 14, fraction);
          // The outermost labels press against the arc ends, so we push them deeper.
          const edge = index === 0 || index === DECADES.length - 1;
          const label = polar(R_SCALE - (edge ? 52 : 32), fraction);
          return (
            <g key={decade} className="gauge-tick">
              <line x1={outer.x} y1={outer.y} x2={inner.x} y2={inner.y} />
              <text x={label.x} y={label.y} dominantBaseline="middle" textAnchor="middle">
                {compact(10 ** decade)}
              </text>
            </g>
          );
        })}

        {/* The needle is drawn to the left (the zero position) and rotated: the CSS
            transition works with transform, and it does not animate the x1/y1/x2/y2
            attributes of a line - the needle jumped because of them. */}
        <line
          className="gauge-needle"
          x1={CX + 16}
          y1={CY}
          x2={CX - (R_SCALE - 18)}
          y2={CY}
          style={{
            transform: `rotate(${position * 180}deg)`,
            transformOrigin: `${CX}px ${CY}px`,
          }}
        />
        <circle className="gauge-hub" cx={CX} cy={CY} r={9} />
      </svg>

      <figcaption>
        <strong className="gauge-value">{compact(value)}</strong>
        <span className="gauge-unit">{t("gauge.unit")}</span>
        <span className="gauge-caption">{caption}</span>
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
