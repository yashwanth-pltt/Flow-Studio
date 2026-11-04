import * as React from "react"

import { getAppViewport, subscribeAppViewport } from "@/lib/app-viewport"

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(undefined)

  React.useEffect(() => {
    // Test the app's layout stage, not the display (see lib/app-viewport.ts).
    const onChange = () => {
      setIsMobile(getAppViewport().width < MOBILE_BREAKPOINT)
    }
    onChange()
    return subscribeAppViewport(onChange)
  }, [])

  return !!isMobile
}
