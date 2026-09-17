import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { useAuth, useUser } from "@clerk/clerk-react";

import { trackEvent } from "@/analytics";
import apiClient, {
  clearTokens,
  getAccessToken,
  ResponseError,
  setAsyncTokenGetter,
  setTokens,
} from "@/client";
import { config } from "@/config";
import { setStoredProjectId } from "@/hooks/use-project-search-sync";
import { getContext } from "@/integrations/tanstack-query";
import { emitGuestUpgrade, hasGuestSession, markGuestSession } from "@/lib/guest";
import { notify } from "@/lib/notify";

type AuthUser = {
  id: string;
  email: string;
  name: string;
  picture: string | null;
};

type AuthContextValue = {
  isSignedIn: boolean;
  isLoaded: boolean;
  isGuest: boolean;
  user: AuthUser | null;
  getToken: () => Promise<string | null>;
  signOut: () => Promise<void>;
  refreshAuth: () => void;
  requestUpgrade: () => void;
  beginGuestSession: (access: string, refresh: string, projectId: string) => void;
};

const AuthContext = createContext<AuthContextValue>({
  beginGuestSession: () => {},
  getToken: async () => null,
  isGuest: false,
  isLoaded: false,
  isSignedIn: false,
  refreshAuth: () => {},
  requestUpgrade: () => {},
  signOut: async () => {},
  user: null,
});

export function useAuthContext() {
  return useContext(AuthContext);
}

function ClerkAuthProvider({ children }: { children: React.ReactNode }) {
  const { isSignedIn: clerkSignedIn, isLoaded, getToken, signOut } = useAuth();
  const { user } = useUser();
  const [guestActive, setGuestActive] = useState(hasGuestSession);
  const [claiming, setClaiming] = useState(false);
  const claimingRef = useRef(false);

  const isGuest = guestActive && !clerkSignedIn;
  const isSignedIn = !!clerkSignedIn || guestActive;

  // A guest runs on the stored JWT pair so client.ts can refresh it; Clerk's
  // getter would switch that flow off.
  useEffect(() => {
    setAsyncTokenGetter(guestActive ? null : () => getToken());
  }, [getToken, guestActive]);

  useEffect(() => {
    if (!clerkSignedIn || !guestActive || claimingRef.current) return;
    claimingRef.current = true;
    setClaiming(true);
    void (async () => {
      let settled = true;
      try {
        const token = await getToken();
        if (!token) return;
        const claimed = await apiClient.auth.authGuestClaimCreate({
          guestClaimRequest: { clerkToken: token },
        });
        setStoredProjectId(claimed.projectId);
        trackEvent("guest_claimed");
      } catch (err) {
        // A refusal is final; a transport failure keeps the guest so a reload retries.
        settled = err instanceof ResponseError && err.response.status < 500;
        notify.error(err, "Could not attach the demo workspace to this account.");
      } finally {
        if (settled) {
          // Synchronous, before the state flip: the re-render fires queries at
          // once, and they must carry the Clerk token, not nothing.
          setAsyncTokenGetter(() => getToken());
          clearTokens();
          setGuestActive(false);
          void getContext().queryClient.invalidateQueries();
        }
        claimingRef.current = false;
        setClaiming(false);
      }
    })();
  }, [clerkSignedIn, getToken, guestActive]);

  const authUser: AuthUser | null =
    clerkSignedIn && user
      ? {
          email: user.primaryEmailAddress?.emailAddress ?? "",
          id: user.id,
          name: user.fullName ?? user.firstName ?? "",
          picture: user.imageUrl ?? null,
        }
      : null;

  const handleSignOut = useCallback(async () => {
    clearTokens();
    setGuestActive(false);
    await signOut();
  }, [signOut]);

  const beginGuestSession = useCallback((access: string, refresh: string, projectId: string) => {
    setTokens(access, refresh);
    markGuestSession(projectId);
    setAsyncTokenGetter(null);
    setGuestActive(true);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        beginGuestSession,
        getToken: isGuest ? async () => getAccessToken() : getToken,
        isGuest,
        isLoaded: isLoaded && !claiming,
        isSignedIn,
        refreshAuth: () => setGuestActive(hasGuestSession()),
        requestUpgrade: emitGuestUpgrade,
        signOut: handleSignOut,
        user: authUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

function readIsSignedIn(): boolean {
  if (typeof window === "undefined") return false;
  return !!localStorage.getItem("jwt_access");
}

function SelfHostedAuthProvider({ children }: { children: React.ReactNode }) {
  const [isSignedIn, setIsSignedIn] = useState(readIsSignedIn);
  const [isGuest, setIsGuest] = useState(hasGuestSession);
  const [user, setUser] = useState<AuthUser | null>(null);

  const refreshAuth = useCallback(() => {
    const signedIn = readIsSignedIn();
    setIsSignedIn(signedIn);
    setIsGuest(hasGuestSession());
    if (!signedIn) {
      setUser(null);
      return;
    }
    void apiClient.auth
      .authMeRetrieve()
      .then((me) => {
        setUser({
          email: me.email,
          id: String(me.id),
          name: me.email,
          picture: null,
        });
      })
      .catch(() => {
        setUser(null);
      });
  }, []);

  useEffect(() => {
    refreshAuth();
    const onStorage = () => refreshAuth();
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [refreshAuth]);

  const getToken = useCallback(async () => localStorage.getItem("jwt_access"), []);

  const handleSignOut = useCallback(async () => {
    clearTokens();
    setIsSignedIn(false);
    setIsGuest(false);
    setUser(null);
  }, []);

  const beginGuestSession = useCallback((access: string, refresh: string, projectId: string) => {
    setTokens(access, refresh);
    markGuestSession(projectId);
    setIsSignedIn(true);
    setIsGuest(true);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        beginGuestSession,
        getToken,
        isGuest,
        isLoaded: true,
        isSignedIn,
        refreshAuth,
        requestUpgrade: emitGuestUpgrade,
        signOut: handleSignOut,
        user,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  if (config.clerkReady) {
    return <ClerkAuthProvider>{children}</ClerkAuthProvider>;
  }
  return <SelfHostedAuthProvider>{children}</SelfHostedAuthProvider>;
}
