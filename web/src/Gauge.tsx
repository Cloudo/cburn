// Прибор расхода. Шкала логарифмическая: burn rate живой машины гуляет от
// тысяч до десятков миллионов токенов в минуту, и на линейной шкале стрелка
// либо лежит на нуле, либо упирается в упор.
//
// Кольцо под шкалой разбито на доли составляющих: чтение кэша обычно на два
// порядка больше остальных, и без разбивки прибор показывал бы только его.

import { useState, type MouseEvent } from "react";

import { compact, grouped, useSmoothNumber } from "./format";

export type Slice = { key: string; label: string; value: number; color: string };

const DECADES = [3, 4, 5, 6, 7]; // 1 тыс … 10 млн токенов в минуту
const MIN = 10 ** DECADES[0];
const MAX = 10 ** DECADES[DECADES.length - 1];

const CX = 200;
const CY = 190;
const R_SCALE = 148;
const R_RING_OUTER = 176;
const R_RING_INNER = 168;

function polar(radius: number, fraction: number) {
  const angle = Math.PI * (1 + fraction); // 0 — слева, 1 — справа
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

/** Позиция значения на логарифмической шкале, 0…1. */
export function scalePosition(value: number): number {
  if (value <= MIN) return 0;
  if (value >= MAX) return 1;
  const span = Math.log10(MAX) - Math.log10(MIN);
  return (Math.log10(value) - Math.log10(MIN)) / span;
}

type Props = { value: number; slices: Slice[]; caption: string };

export function Gauge({ value, slices, caption }: Props) {
  const smooth = useSmoothNumber(value);
  const position = scalePosition(smooth);
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
          // Крайние метки прижимаются к торцам дуги, поэтому их сдвигаем глубже.
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

        {/* Стрелка рисуется влево (положение нуля) и поворачивается: CSS-переход
            работает с transform, а атрибуты x1/y1/x2/y2 линии он не анимирует —
            от них стрелка прыгала. */}
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
        <strong className="gauge-value">{compact(smooth)}</strong>
        <span className="gauge-unit">токенов в минуту</span>
        <span className="gauge-caption">{caption}</span>
      </figcaption>
    </figure>
  );
}

/** Линейная шкала выходных токенов: их немного, и логарифм тут только мешает. */
export function OutputMeter({ value, peak }: { value: number; peak: number }) {
  const smooth = useSmoothNumber(value);
  const ceiling = Math.max(peak, 1000);
  const share = Math.min(smooth / ceiling, 1);
  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-label">выход модели</span>
        <span className="meter-value">
          {compact(smooth)} <span className="meter-unit">ток/мин</span>
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

/** Самописец: расход по корзинам времени. Шаг задаёт сервер (сейчас 5 секунд). */
export function Recorder({
  series,
  bucketSeconds,
}: {
  series: Array<{ at: string; tokens: number; output_tokens: number; turns: number }>;
  bucketSeconds: number;
}) {
  // Высота — по выходу модели: в суммарных токенах любой ход выглядит
  // одинаково, потому что чтение кэша перевешивает всё остальное.
  const peak = Math.max(...series.map((bucket) => bucket.output_tokens), 1);
  const span = Math.round((series.length * bucketSeconds) / 60);
  const [hover, setHover] = useState<number | null>(null);

  // Корзина ищется по позиции курсора на всей дорожке, а не наведением на сам
  // столбик: пустые корзины высотой в пиксель иначе не поймать.
  const track = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const index = Math.floor(((event.clientX - box.left) / box.width) * series.length);
    setHover(Math.min(Math.max(index, 0), series.length - 1));
  };

  const active = hover === null ? null : series[hover];

  return (
    <div className="recorder">
      <div className="recorder-head">
        <span className="recorder-label">выход по {bucketSeconds} с</span>
        <span className="recorder-span">последние {span} мин</span>
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
          // Подсказка стоит у края дорожки, противоположного курсору: следуя
          // за ним, она вылезала бы за панель и дёргалась при каждом движении.
          <div
            className={
              hover! < series.length / 2 ? "recorder-tip recorder-tip-right" : "recorder-tip"
            }
          >
            <span className="recorder-tip-time">{bucketClock(active.at, bucketSeconds)}</span>
            <span className="recorder-tip-row">
              выход <strong>{grouped(active.output_tokens)}</strong>
            </span>
            <span className="recorder-tip-row">
              всего <strong>{compact(active.tokens)}</strong>
            </span>
            <span className="recorder-tip-row">
              ходов <strong>{active.turns}</strong>
            </span>
          </div>
        )}
      </div>
      <div className="recorder-scale">
        <span>−{span} мин</span>
        <span>пик {compact(peak)} за корзину</span>
        <span>сейчас</span>
      </div>
    </div>
  );
}

/** Подпись корзины: интервал, который она покрывает. */
function bucketClock(at: string, seconds: number): string {
  const from = new Date(at);
  const to = new Date(from.getTime() + seconds * 1000);
  const clock = (date: Date) =>
    date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${clock(from)} — ${clock(to)}`;
}
