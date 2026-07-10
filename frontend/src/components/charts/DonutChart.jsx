import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';
import { chartColors, tooltipStyle } from './chartTheme';

/**
 * Project DonutChart.
 * @param {Array} data - [{ label, value }]
 * @param {(label:string)=>string} colorFor - optional color resolver
 * @param {string} ariaLabel - accessible name; the SVG itself has none, so
 *   without this a screen reader gets nothing meaningful from the chart.
 */
export const DonutChart = ({
  data = [],
  height = 260,
  formatValue = (v) => v,
  colorFor,
  ariaLabel = 'Donut chart',
}) => (
  <div role="img" aria-label={ariaLabel}>
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="label"
          innerRadius="55%"
          outerRadius="80%"
          paddingAngle={2}
          stroke="none"
        >
          {data.map((d, i) => (
            <Cell
              key={i}
              fill={colorFor ? colorFor(d.label) : chartColors.palette[i % chartColors.palette.length]}
            />
          ))}
        </Pie>
        <Tooltip contentStyle={tooltipStyle} formatter={(v) => formatValue(v)} />
        <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
      </PieChart>
    </ResponsiveContainer>
  </div>
);

export default DonutChart;
