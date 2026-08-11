"use client";

type Chip = {
  id: string;
  label: string;
};

export function SymbolChips({
  items,
  value,
  onChange,
}: {
  items: Chip[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="-mx-4 mt-6 overflow-x-auto px-4 scrollbar-none sm:mx-0 sm:flex sm:flex-wrap sm:gap-2 sm:overflow-visible sm:px-0">
      <div className="flex w-max gap-2 sm:w-auto sm:flex-wrap">
        {items.map((item) => {
          const active = value === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange(item.id)}
              className={`min-h-11 shrink-0 rounded-md px-3 py-2 text-sm ${
                active
                  ? "bg-ink text-paper"
                  : "bg-white/70 text-ink/80 hover:bg-white"
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
