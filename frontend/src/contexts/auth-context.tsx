import { createContext, useContext, useEffect, useState } from "react";

import { useAuth, useOrganization } from "@clerk/clerk-react";

type AuthContextValue = {
  organisationId: string;
  isSignedIn: boolean;
};

const AuthContext = createContext<AuthContextValue>({
  isSignedIn: false,
  organisationId: "",
});

export function useAuthContext() {
  return useContext(AuthContext);
}

function readIsSignedInFromStorage(): boolean {
  if (typeof window === "undefined") return false;
  return !!(localStorage.getItem("token") ?? localStorage.getItem("auth_token"));
}

export function FallbackAuthProvider({ children }: { children: React.ReactNode }) {
  const [isSignedIn, setIsSignedIn] = useState(readIsSignedInFromStorage);

  useEffect(() => {
    const check = () => setIsSignedIn(readIsSignedInFromStorage);
    window.addEventListener("storage", check);
    return () => window.removeEventListener("storage", check);
  }, []);

  return (
    <AuthContext.Provider value={{ isSignedIn, organisationId: "" }}>
      {children}
    </AuthContext.Provider>
  );
}

export function ClerkAuthProvider({ children }: { children: React.ReactNode }) {
  const { organization } = useOrganization();
  const { isSignedIn } = useAuth();
  return (
    <AuthContext.Provider
      value={{
        isSignedIn: isSignedIn ?? false,
        organisationId: organization?.id ?? "",
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
