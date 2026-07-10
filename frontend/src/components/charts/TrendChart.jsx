import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { chartColors, axisProps, tooltipStyle } from './chartTheme';

/**
 * Project TrendChart — library-agnostic contract.
 * @param {Array} data - [{ [xKey], [valueKey] }]
 * @param {string} ariaLabel - accessible name; the SVG itself has none, so
 *   without this a screen reader gets nothing meaningful from the chart.
 */
export const TrendChart = ({
  data = [],
  xKey = 'period',
  valueKey = 'value',
  height = 260,
  formatValue = (v) => v,
  ariaLabel = 'Trend chart',
}) => (
  <div role="img" aria-label={ariaLabel}>
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="st-trend-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={chartColors.brand} stopOpacity={0.35} />
            <stop offset="100%" stopColor={chartColors.brand} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={chartColors.grid} vertical={false} />
        <XAxis dataKey={xKey} {...axisProps} />
        <YAxis {...axisProps} width={48} tickFormatter={formatValue} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v) => formatValue(v)} />
        <Area
          type="monotone"
          dataKey={valueKey}
          stroke={chartColors.brand}
          strokeWidth={2}
          fill="url(#st-trend-fill)"
        />
      </AreaChart>
    </ResponsiveContainer>
  </div>
);

export default TrendChart;
