const GOOGLE_OAUTH_START_URL =
  import.meta.env.VITE_GOOGLE_AUTH_START_URL ??
  `https://api.overmindlab.ai/api/v1/oauth/google/start`;

export function ContinueWithGoogle({ className }: { className?: string }) {
  const handleContinueWithGoogle = () => {
    window.location.href = GOOGLE_OAUTH_START_URL;
  };

  return (
    <button
      className={
        className ??
        "flex h-12 w-full items-center justify-center gap-3 rounded-md border border-[#3A3530] bg-[#252220] text-sm font-medium text-white transition-colors hover:border-[#4A4540] hover:bg-[#302E2C]"
      }
      onClick={handleContinueWithGoogle}
      type="button"
    >
      <svg aria-hidden="true" className="size-5" focusable="false" viewBox="0 0 512 512">
        <g>
          <path
            d="M502.657 261.286c0-17.625-1.572-34.714-4.51-51.143H260v96.771h137.009c-5.927 32-23.885 59.02-50.942 77.135l82.215 63.99c48.093-44.438 74.375-109.836 74.375-186.753z"
            fill="#4285F4"
          />
          <path
            d="M260 508c66.42 0 122.198-21.91 162.93-59.653l-82.216-63.99c-22.989 15.411-52.327 24.654-80.714 24.654-61.984 0-114.416-41.89-133.324-98.155l-86.049 66.443C77.48 465.345 158.693 508 260 508z"
            fill="#34A853"
          />
          <path
            d="M126.676 310.855c-7.609-22.934-7.609-47.47 0-70.404l-86.049-66.443C11.064 212.808 0 235.884 0 260s11.064 47.19 40.627 86.992l86.049-66.442z"
            fill="#FBBC05"
          />
          <path
            d="M260 101.667c35.924 0 69.054 12.366 94.837 36.611l71.065-71.065C382.197 28.07 326.42 4 260 4 158.693 4 77.48 46.655 40.627 133.008l86.049 66.443C145.584 143.557 198.016 101.667 260 101.667z"
            fill="#EA4335"
          />
        </g>
      </svg>
      Continue with Google
    </button>
  );
}
