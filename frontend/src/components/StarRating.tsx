"use client";
import { useState } from "react";

interface Props {
  value: number;
  onChange?: (v: number) => void;
  size?: "sm" | "md" | "lg";
  readonly?: boolean;
}

const sizes = { sm: "text-base", md: "text-2xl", lg: "text-3xl" };

export function StarRating({ value, onChange, size = "md", readonly = false }: Props) {
  const [hovered, setHovered] = useState(0);
  const display = hovered || value;

  return (
    <div className="flex gap-0.5">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          disabled={readonly}
          onClick={() => onChange?.(n)}
          onMouseEnter={() => !readonly && setHovered(n)}
          onMouseLeave={() => !readonly && setHovered(0)}
          className={`${sizes[size]} ${readonly ? "cursor-default" : "cursor-pointer"} transition-transform ${!readonly && "hover:scale-110"}`}
        >
          <span className={display >= n ? "text-yellow-400" : "text-gray-600"}>★</span>
        </button>
      ))}
    </div>
  );
}
