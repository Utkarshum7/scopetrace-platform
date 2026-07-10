/**
 * Shared enterprise card surface (dark glassmorphic panel), previously
 * hand-copied with drifting padding/gap into RecordsPage, UploadPage,
 * KpiCard, and WidgetFrame. Callers own padding/gap/layout via className;
 * `as` lets non-div callers (e.g. a labeled region) keep their semantics.
 */
export const Card = ({ as: Tag = 'div', className = '', children, ...rest }) => (
  <Tag
    className={`bg-slate-800/40 backdrop-blur-xl border border-slate-700/50 rounded-xl shadow-lg transition-all duration-300 ${className}`}
    {...rest}
  >
    {children}
  </Tag>
);

export default Card;
