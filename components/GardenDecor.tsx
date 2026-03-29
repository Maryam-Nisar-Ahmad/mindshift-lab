'use client';

/**
 * Decorative garden layer only — pointer-events-none everywhere.
 */
export default function GardenDecor() {
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden>
      <div
        className="absolute inset-0 opacity-50"
        style={{
          background:
            'radial-gradient(ellipse 100% 70% at 10% 20%, rgba(255, 196, 221, 0.35) 0%, transparent 50%), radial-gradient(ellipse 80% 60% at 90% 10%, rgba(216, 240, 252, 0.45) 0%, transparent 45%), radial-gradient(ellipse 60% 50% at 50% 100%, rgba(197, 236, 212, 0.4) 0%, transparent 50%)',
        }}
      />

      <svg
        className="fun-decor-drift absolute -left-6 top-12 h-48 w-48 opacity-[0.38]"
        viewBox="0 0 120 120"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <g fill="var(--garden-petal)">
          <ellipse cx="60" cy="38" rx="14" ry="20" transform="rotate(0 60 58)" />
          <ellipse cx="60" cy="38" rx="14" ry="20" transform="rotate(72 60 58)" />
          <ellipse cx="60" cy="38" rx="14" ry="20" transform="rotate(144 60 58)" />
          <ellipse cx="60" cy="38" rx="14" ry="20" transform="rotate(216 60 58)" />
          <ellipse cx="60" cy="38" rx="14" ry="20" transform="rotate(288 60 58)" />
        </g>
        <circle cx="60" cy="58" r="11" fill="var(--garden-petal-deep)" />
        <path
          d="M58 72 Q52 92 46 112"
          stroke="var(--garden-leaf-stem)"
          strokeWidth="4"
          strokeLinecap="round"
        />
        <ellipse cx="44" cy="96" rx="14" ry="7" transform="rotate(-32 44 96)" fill="var(--garden-leaf)" />
        <ellipse cx="56" cy="104" rx="11" ry="6" transform="rotate(28 56 104)" fill="var(--garden-leaf-soft)" />
      </svg>

      <svg
        className="fun-decor-drift-delayed absolute -right-4 top-6 h-40 w-52 opacity-[0.34]"
        viewBox="0 0 160 110"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M15 95 Q38 48 105 28 Q138 22 152 38 Q118 52 88 58 Q48 72 15 95Z"
          fill="var(--garden-leaf)"
        />
        <path
          d="M25 100 Q52 58 125 50 Q148 55 158 72 Q102 78 62 88 Z"
          fill="var(--garden-leaf-soft)"
          opacity="0.85"
        />
        <circle cx="128" cy="30" r="7" fill="var(--garden-petal-deep)" opacity="0.45" />
        <circle cx="118" cy="24" r="4" fill="var(--garden-petal)" opacity="0.5" />
      </svg>

      <svg
        className="fun-decor-drift-slow absolute bottom-10 left-[6%] h-36 w-36 opacity-[0.34]"
        viewBox="0 0 100 100"
        xmlns="http://www.w3.org/2000/svg"
      >
        {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
          <ellipse
            key={deg}
            cx="50"
            cy="34"
            rx="7"
            ry="16"
            transform={`rotate(${deg} 50 50)`}
            fill="var(--garden-lilac)"
            opacity="0.9"
          />
        ))}
        <circle cx="50" cy="50" r="9" fill="var(--garden-petal-deep)" />
      </svg>

      <svg
        className="fun-decor-drift absolute bottom-8 right-[4%] h-44 w-40 opacity-[0.36]"
        style={{ animationDelay: '-1.2s' }}
        viewBox="0 0 90 120"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M45 118 L45 76" stroke="var(--garden-leaf-stem)" strokeWidth="5" strokeLinecap="round" />
        <path
          d="M45 76 C18 54 12 28 45 16 C78 28 72 54 45 76Z"
          fill="var(--garden-petal)"
          opacity="0.75"
        />
        <ellipse cx="30" cy="96" rx="15" ry="7" transform="rotate(-28 30 96)" fill="var(--garden-leaf)" />
        <ellipse cx="60" cy="100" rx="12" ry="6" transform="rotate(32 60 100)" fill="var(--garden-leaf-soft)" />
      </svg>

      <div className="absolute left-[42%] top-28 h-3 w-3 rounded-full bg-[var(--garden-petal)] opacity-30" />
      <div className="absolute right-[32%] top-44 h-2 w-2 rounded-full bg-[var(--garden-petal-deep)] opacity-35" />
      <div className="absolute left-[58%] bottom-36 h-2.5 w-2.5 rounded-full bg-[var(--garden-lilac)] opacity-28" />
    </div>
  );
}
