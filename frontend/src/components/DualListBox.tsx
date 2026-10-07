"use client";

import { useMemo, useState } from "react";

interface Item {
  id: number;
  label: string;
}

/**
 * Django admin's "Available / Chosen" filter-horizontal widget: two filterable
 * lists, click to highlight, arrows (or double-click) to move, "Choose all" /
 * "Remove all". Controlled by the set of chosen ids.
 */
export function DualListBox({
  availableTitle,
  chosenTitle,
  items,
  chosen,
  onChange,
}: {
  availableTitle: string;
  chosenTitle: string;
  items: Item[];
  chosen: number[];
  onChange: (chosen: number[]) => void;
}) {
  const [leftFilter, setLeftFilter] = useState("");
  const [rightFilter, setRightFilter] = useState("");
  const [leftPick, setLeftPick] = useState<Set<number>>(new Set());
  const [rightPick, setRightPick] = useState<Set<number>>(new Set());

  const chosenSet = useMemo(() => new Set(chosen), [chosen]);
  const left = items.filter((i) => !chosenSet.has(i.id) && i.label.toLowerCase().includes(leftFilter.toLowerCase()));
  const right = items.filter((i) => chosenSet.has(i.id) && i.label.toLowerCase().includes(rightFilter.toLowerCase()));

  const toggle = (set: Set<number>, id: number, apply: (s: Set<number>) => void) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    apply(next);
  };
  const add = (ids: number[]) => {
    onChange([...chosen, ...ids.filter((id) => !chosenSet.has(id))]);
    setLeftPick(new Set());
  };
  const remove = (ids: number[]) => {
    const drop = new Set(ids);
    onChange(chosen.filter((id) => !drop.has(id)));
    setRightPick(new Set());
  };

  const box = "flex h-56 flex-col overflow-hidden rounded-md border border-[var(--border)]";
  const row = (item: Item, picked: boolean, onClick: () => void, onDouble: () => void) => (
    <li
      key={item.id}
      onClick={onClick}
      onDoubleClick={onDouble}
      className={`cursor-pointer select-none px-2 py-0.5 text-sm ${picked ? "bg-accent text-white" : "hover:bg-black/5 dark:hover:bg-white/5"}`}
    >
      {item.label}
    </li>
  );

  return (
    <div className="flex flex-wrap items-start gap-2">
      <div className="w-full max-w-md flex-1">
        <div className={box}>
          <div className="bg-black/5 px-2 py-1 text-sm font-medium dark:bg-white/5">{availableTitle}</div>
          <input value={leftFilter} onChange={(e) => setLeftFilter(e.target.value)} placeholder="Filter" className="m-1 rounded border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none" />
          <ul className="min-h-0 flex-1 overflow-y-auto">
            {left.map((i) => row(i, leftPick.has(i.id), () => toggle(leftPick, i.id, setLeftPick), () => add([i.id])))}
          </ul>
        </div>
        <button type="button" onClick={() => add(left.map((i) => i.id))} className="mt-1 w-full text-center text-sm font-semibold hover:text-accent">
          Choose all ›
        </button>
      </div>
      <div className="flex flex-col gap-1 self-center">
        <button type="button" onClick={() => add([...leftPick])} aria-label="Добавить выбранные" className="rounded border border-[var(--border)] px-2 py-1 hover:border-accent">›</button>
        <button type="button" onClick={() => remove([...rightPick])} aria-label="Убрать выбранные" className="rounded border border-[var(--border)] px-2 py-1 hover:border-accent">‹</button>
      </div>
      <div className="w-full max-w-md flex-1">
        <div className={box}>
          <div className="bg-accent/20 px-2 py-1 text-sm font-medium">{chosenTitle}</div>
          <input value={rightFilter} onChange={(e) => setRightFilter(e.target.value)} placeholder="Filter" className="m-1 rounded border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none" />
          <ul className="min-h-0 flex-1 overflow-y-auto">
            {right.map((i) => row(i, rightPick.has(i.id), () => toggle(rightPick, i.id, setRightPick), () => remove([i.id])))}
          </ul>
        </div>
        <button type="button" onClick={() => remove(chosen)} className="mt-1 w-full text-center text-sm font-semibold hover:text-accent">
          ‹ Remove all
        </button>
      </div>
    </div>
  );
}
