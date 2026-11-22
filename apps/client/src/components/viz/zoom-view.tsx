import { IconFocusCentered, IconMinus, IconPlus } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import { Button } from "@/components/ui/button.tsx";
import { m } from "@/paraglide/messages.js";

/** Content that pans by dragging and zooms by wheel, pinch, double click or the buttons. */
export default function ZoomView({ children }: { children: ReactNode }) {
  return (
    <TransformWrapper
      centerOnInit
      doubleClick={{ mode: "toggle" }}
      limitToBounds={false}
      maxScale={8}
      minScale={0.2}
    >
      {({ zoomIn, zoomOut, resetTransform }) => (
        <div className="bg-background relative h-full overflow-hidden rounded-md border">
          <div className="bg-popover absolute top-2 right-2 z-10 flex gap-0.5 rounded-md border p-0.5">
            <Button
              aria-label={m.data_zoom_out()}
              onClick={() => zoomOut()}
              size="icon-sm"
              variant="ghost"
            >
              <IconMinus />
            </Button>
            <Button
              aria-label={m.data_zoom_in()}
              onClick={() => zoomIn()}
              size="icon-sm"
              variant="ghost"
            >
              <IconPlus />
            </Button>
            <Button
              aria-label={m.data_reset_zoom()}
              onClick={() => resetTransform()}
              size="icon-sm"
              variant="ghost"
            >
              <IconFocusCentered />
            </Button>
          </div>
          <TransformComponent
            contentClass="w-full! justify-center p-6"
            wrapperClass="h-full! w-full! cursor-grab active:cursor-grabbing"
          >
            {children}
          </TransformComponent>
        </div>
      )}
    </TransformWrapper>
  );
}
