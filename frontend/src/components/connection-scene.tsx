import type { CSSProperties, ReactNode } from "react";

import { Card } from "@/components/ui/card";
import styles from "./connection-scene.module.css";

const ASTEROIDS = [
  { delay: 0, duration: 6, left: "78%", size: 280, top: "-6%" },
  { delay: -2, duration: 8, left: "108%", size: 120, top: "12%" },
  { delay: -4, duration: 5, left: "52%", size: 90, top: "-4%" },
  { delay: -1, duration: 7, left: "92%", size: 140, top: "4%" },
  { delay: -6, duration: 9, left: "38%", size: 100, top: "-2%" },
];

export function ConnectionScene({ children }: { children: ReactNode }) {
  return (
    <main className={`dark ${styles.scene} bg-card text-foreground`}>
      <div aria-hidden="true" className={styles.art}>
        <img alt="" className={styles.landscape} src="/connection/stars-v2.jpg" />
        {ASTEROIDS.map((asteroid) => (
          <div
            className={styles.asteroid}
            key={asteroid.left}
            style={
              {
                "--delay": `${asteroid.delay}s`,
                "--duration": `${asteroid.duration}s`,
                left: asteroid.left,
                top: asteroid.top,
              } as CSSProperties
            }
          >
            <img
              alt=""
              className="rotate-12 object-contain opacity-80"
              height={asteroid.size}
              src="/connection/asteroid.png"
              width={asteroid.size}
            />
          </div>
        ))}
        <img alt="" className={styles.landscape} src="/connection/trees.png" />
        <div className={styles.scanlines} />
      </div>
      <div className={styles.content}>{children}</div>
    </main>
  );
}

export function ConnectionPanel({ children }: { children: ReactNode }) {
  return (
    <div className="relative w-full max-w-xl p-3">
      <div aria-hidden="true" className={styles.frame}>
        <div className={styles.edge} />
        <div className={styles.sides} />
        <div className={styles.edge} />
      </div>
      <Card className="relative space-y-6 rounded-none border-0 bg-auth-panel p-5 sm:p-7">
        {children}
      </Card>
    </div>
  );
}

export const connectionHeading = styles.heading;
