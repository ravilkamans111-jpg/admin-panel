"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export default function RootPage() {
  const { loading, isAuthenticated } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    router.replace(isAuthenticated ? "/dashboard" : "/login");
  }, [loading, isAuthenticated, router]);

  return null;
}
