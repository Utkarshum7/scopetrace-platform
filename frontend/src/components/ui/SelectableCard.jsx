/**
 * Shared radio-style selectable card, extracted from UploadPage's three
 * near-identical adapter-strategy cards. Renders as role="radio" (not a
 * real <input type="radio">) because it needs to host a title/description
 * layout a native radio can't -- Enter/Space activation is handled here so
 * every caller gets identical keyboard behavior for free.
 */
export const SelectableCard = ({ selected, onSelect, eyebrow, title, description, className = '' }) => {
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <div
      onClick={onSelect}
      onKeyDown={handleKeyDown}
      role="radio"
      aria-checked={selected}
      tabIndex={0}
      className={`cursor-pointer bg-slate-800/40 backdrop-blur-xl border rounded-xl p-5 shadow-lg flex flex-col gap-3 transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
        selected
          ? 'border-brand-500 bg-slate-800/80 shadow-brand-500/5'
          : 'border-slate-700/50 hover:border-slate-600 hover:bg-slate-800/20'
      } ${className}`}
    >
      <div className="flex justify-between items-center">
        <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">{eyebrow}</span>
        <span className={`w-2.5 h-2.5 rounded-full ${selected ? 'bg-brand-500 shadow-[0_0_8px_#10b981]' : 'bg-slate-600'}`} />
      </div>
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-bold text-white">{title}</h3>
        <p className="text-xs text-slate-400 leading-relaxed">{description}</p>
      </div>
    </div>
  );
};

export default SelectableCard;
