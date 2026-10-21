import { useState } from 'react';
import { fmt } from './lib';
import type { Sample } from './types';

export function ErrorChart({ samples, height = 130 }: { samples: Sample[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 800, left = 36, right = 15, top = 14, bottom = 24;
  const maximum = Math.max(1, ...samples.flatMap(s => [s.baseline ?? 0, s.proposed ?? 0, s.ai ?? 0]));
  const ceiling = Math.ceil(maximum * 1.1 * 2) / 2;
  const start = samples[0]?.t ?? 0, end = Math.max(start + 1, samples.at(-1)?.t ?? 10);
  const x = (t: number) => left + (t - start) / (end - start) * (width - left - right);
  const y = (v: number) => top + (1 - v / ceiling) * (height - top - bottom);
  const path = (key: 'baseline' | 'proposed' | 'ai') => {
    let pen = false;
    return samples.map(s => { const value = s[key]; if (value == null) { pen = false; return ''; } const command = pen ? 'L' : 'M'; pen = true; return `${command}${x(s.t)},${y(value)}`; }).join(' ');
  };
  const selected = hover == null ? null : samples[Math.max(0, Math.min(samples.length - 1, hover))];
  return <div className="chart-wrapper">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0} aria-label="Actual baseline, LOCUS-X, and LOCUS-X plus AI localization error over simulation time. Use arrow keys to inspect recorded samples." onFocus={() => setHover(samples.length - 1)} onBlur={() => setHover(null)} onKeyDown={event => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setHover(current => Math.max(0, Math.min(samples.length - 1, (current ?? 0) + (event.key === 'ArrowLeft' ? -1 : 1)))); }
    }} onMouseLeave={() => setHover(null)} onMouseMove={event => {
      const bounds = event.currentTarget.getBoundingClientRect();
      const px = (event.clientX - bounds.left) / bounds.width * width;
      const time = start + (px - left) / (width - left - right) * (end - start);
      setHover(samples.reduce((nearest, sample, index) => Math.abs(sample.t - time) < Math.abs(samples[nearest].t - time) ? index : nearest, 0));
    }}>
      {[0, 0.5, 1].map(f => <g key={f}><line x1={left} x2={width - right} y1={y(ceiling * f)} y2={y(ceiling * f)} stroke="#304338" strokeDasharray="3 5" /><text x={left - 9} y={y(ceiling * f) + 4} textAnchor="end">{fmt(ceiling * f, 1)}</text></g>)}
      {[0, 0.25, 0.5, 0.75, 1].map(f => <text key={f} x={x(start + f * (end - start))} y={height - 3} textAnchor="middle">{fmt(start + f * (end - start), 0)}s</text>)}
      <path d={path('baseline')} stroke="#dcaf6c" fill="none" strokeWidth="1.7" vectorEffect="non-scaling-stroke" />
      <path d={path('proposed')} stroke="#86d9ac" fill="none" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <path d={path('ai')} stroke="#a99bea" fill="none" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
      {samples.length === 1 && <>{(['baseline', 'proposed', 'ai'] as const).map(key => samples[0][key] != null && <circle key={key} cx={x(samples[0].t)} cy={y(samples[0][key]!)} r="3" fill={key === 'baseline' ? '#dcaf6c' : key === 'proposed' ? '#86d9ac' : '#a99bea'} />)}</>}
      {selected && <line x1={x(selected.t)} x2={x(selected.t)} y1={top} y2={height - bottom} stroke="#749781" strokeDasharray="2 3" />}
    </svg>
    {selected && <div className="chart-tooltip"><span>{fmt(selected.t, 1)} s</span><span className="amber">Baseline {fmt(selected.baseline)} m</span><span className="mint">LOCUS-X {fmt(selected.proposed)} m</span><span className="ai">LOCUS-X + AI {fmt(selected.ai)} m</span></div>}
  </div>;
}

export function CdfChart({ data }: { data: { baseline: number[]; proposed: number[]; ai?: number[] } }) {
  const width = 500, height = 180, left = 35, bottom = 25;
  const maximum = Math.max(1, ...data.baseline, ...data.proposed, ...(data.ai ?? []));
  return <svg className="cdf-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Empirical distribution of valid localization errors">
    {[0, 0.5, 1].map(f => <g key={f}><line x1={left} x2={width - 15} y1={height - bottom - f * 140} y2={height - bottom - f * 140} stroke="#304338" strokeDasharray="3 5" /><text x={left - 8} y={height - bottom - f * 140 + 4} textAnchor="end">{f * 100}%</text></g>)}
    {([['baseline', '#dcaf6c'], ['proposed', '#86d9ac'], ['ai', '#a99bea']] as const).map(([key, color]) => {
      const values = data[key] ?? [];
      return <path key={key} d={values.map((v, i) => `${i ? 'L' : 'M'}${left + v / maximum * (width - left - 15)},${height - bottom - i / Math.max(1, values.length - 1) * 140}`).join(' ')} fill="none" stroke={color} strokeWidth="2" />;
    })}
    {[0, 0.5, 1].map(f => <text key={f} x={left + f * (width - left - 15)} y={height - 3} textAnchor="middle">{fmt(maximum * f, 1)} m</text>)}
  </svg>;
}
