import {
  ResponsiveContainer,
  BarChart as RBarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from 'recharts';
import { chartColors, axisProps, tooltipStyle } from './chartTheme';

/**
 * Project BarChart.
 * @param {Array} data - [{ label, value }]
 * @param {string} ariaLabel - accessible name; the SVG itself has none, so
 *   without this a screen reader gets nothing meaningful from the chart.
 */
export const BarChart = ({
  data = [],
  xKey = 'label',
  valueKey = 'value',
  height = 260,
  formatValue = (v) => v,
  ariaLabel = 'Bar chart',
}) => (
  <div role="img" aria-label={ariaLabel}>
    <ResponsiveContainer width="100%" height={height}>
      <RBarChart data={data} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={chartColors.grid} vertical={false} />
        <XAxis dataKey={xKey} {...axisProps} />
        <YAxis {...axisProps} width={48} tickFormatter={formatValue} />
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v) => formatValue(v)}
          cursor={{ fill: '#1e293b55' }}
        />
        <Bar dataKey={valueKey} radius={[4, 4, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={chartColors.palette[i % chartColors.palette.length]} />
          ))}
        </Bar>
      </RBarChart>
    </ResponsiveContainer>
  </div>
);

export default BarChart;
