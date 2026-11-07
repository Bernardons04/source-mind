import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function updateSession(request: NextRequest) {
  let supabaseResponse = NextResponse.next({
    request,
  });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey =
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ||
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseKey || supabaseUrl.includes("placeholder")) {
    return supabaseResponse;
  }

  const supabase = createServerClient(supabaseUrl, supabaseKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet: Array<{ name: string; value: string; options?: any }>) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        supabaseResponse = NextResponse.next({
          request,
        });
        cookiesToSet.forEach(({ name, value, options }) =>
          supabaseResponse.cookies.set(name, value, options)
        );
      },
    },
  });

  // Do not call getUser() on the callback route itself to avoid race conditions during code exchange
  let user = null;
  if (!request.nextUrl.pathname.startsWith("/auth/callback")) {
    const {
      data: { user: authUser },
    } = await supabase.auth.getUser();
    user = authUser;
  }

  const { pathname, search } = request.nextUrl;

  // Public paths accessible without authentication
  const isPublicRoute =
    pathname === "/" ||
    pathname === "/login" ||
    pathname.startsWith("/auth/callback");

  // If the user is unauthenticated and attempting to access a private route
  if (!user && !isPublicRoute) {
    const redirectUrl = new URL("/login", request.url);
    const destination = `${pathname}${search}`;
    if (destination && destination !== "/") {
      redirectUrl.searchParams.set("next", destination);
    }
    return NextResponse.redirect(redirectUrl);
  }

  // If the user is already authenticated and visits /login, redirect to /assistants
  if (user && pathname === "/login") {
    const nextParam = request.nextUrl.searchParams.get("next") || "/assistants";
    return NextResponse.redirect(new URL(nextParam, request.url));
  }

  return supabaseResponse;
}
