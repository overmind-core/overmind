import { useState } from "react";

import { useClerk, useUser } from "@clerk/clerk-react";

import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function AccountMenu() {
  const { user } = useUser();
  const clerk = useClerk();
  const [open, setOpen] = useState(false);

  if (!user) return null;

  const email = user.primaryEmailAddress?.emailAddress ?? "";
  const name = user.fullName || user.username || email || "Account";

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <button
          aria-label="Account"
          className="flex h-10 w-full min-w-0 items-center gap-2 rounded-md px-1 text-left text-sm text-sidebar-foreground outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:px-0"
          type="button"
        >
          {user.imageUrl ? (
            // The collapsed rail can pinch the row below the avatar's width:
            // aspect-square + object-cover clips instead of distorting.
            <img
              alt=""
              className="aspect-square size-6 shrink-0 rounded-md object-cover"
              height={24}
              referrerPolicy="no-referrer"
              src={user.imageUrl}
              width={24}
            />
          ) : (
            <Icon.user className="size-6 shrink-0" />
          )}
          <span className="min-w-0 flex-1 truncate leading-none group-data-[collapsible=icon]:hidden">
            {name}
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 overflow-hidden p-0" side="top" sideOffset={6}>
        <div className="border-b border-border/70 px-3 py-2">
          <p className="truncate text-sm font-medium text-foreground">{name}</p>
          {email && name !== email ? (
            <p className="truncate text-xs text-muted-foreground">{email}</p>
          ) : null}
        </div>
        <div className="p-1">
          <button
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
            onClick={() => {
              setOpen(false);
              void clerk.openUserProfile();
            }}
            type="button"
          >
            <Icon.user className="size-4 shrink-0 text-muted-foreground" />
            Manage account
          </button>
          <button
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
            onClick={() => {
              setOpen(false);
              void clerk.signOut({ redirectUrl: "/login" });
            }}
            type="button"
          >
            <Icon.logout className="size-4 shrink-0 text-muted-foreground" />
            Sign out
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
