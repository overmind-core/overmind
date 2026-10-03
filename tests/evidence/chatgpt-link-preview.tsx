import { createRoot } from "react-dom/client";
import api from "./src/client";
import { LocalLoginForm } from "./src/components/local-login-form";
import { SplashBackground } from "./src/components/splash-background";
import "./src/styles.css";
api.chatgpt.chatgptLoginRetrieve = async () => ({enabled: true, rememberedEmail: ""});
api.chatgpt.chatgptCallbackSessionRetrieve = async () => ({email: "fixture@example.invalid", requiresPassword: true});
api.chatgpt.chatgptCallbackSessionCreate = async () => { throw new Error("Incorrect Overmind password. Try again."); };
createRoot(document.getElementById("root")!).render(<SplashBackground><div className="mx-4 w-full max-w-sm"><p className="mb-4 text-sm text-auth-text-label">Account-link preview · fixture data</p><LocalLoginForm chatgptComplete onSignedIn={() => {}} /></div></SplashBackground>);
