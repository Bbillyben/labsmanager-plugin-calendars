declare module '@labsmanager/plugin-ui' {
  import type { ComponentType, ReactNode, RefObject, RefAttributes, ButtonHTMLAttributes } from 'react'
  export const PLUGIN_UI_API_VERSION: 1
  export type EntitySearchSource = {
    search: (query: string, signal: AbortSignal) => Promise<{ options: { value: string; label: string }[]; hasMore: boolean }>
    resolve: (id: string, signal: AbortSignal) => Promise<{ value: string; label: string } | null>
  }
  export const EntitySearch: ComponentType<{ id: string; label: string; value: string; source: EntitySearchSource; onChange: (value: string) => void }>
  export const employeeFilterSources: { employees: EntitySearchSource; allEmployees: EntitySearchSource }
  export const teamFilterSources: { teams: EntitySearchSource }
  export const projectFilterSources: { projects: EntitySearchSource }
  export const Button: ComponentType<ButtonHTMLAttributes<HTMLButtonElement> & RefAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'destructive' | 'ghost' | 'outline' }>
  export type EntityAction = { id: string; label: string; icon?: ReactNode; disabled?: boolean; onSelect: () => void }
  export const EntityActionMenu: ComponentType<{ label: string; groups: EntityAction[][] }>
  export const Alert: ComponentType<{ tone?: 'info' | 'success' | 'warning' | 'danger'; children?: ReactNode }>
  export const PluginPublishedCalendarActions: ComponentType<{ providerSlug: string; calendarId: string }>
  export type PluginCalendarEvent = { id: string; title: string; start: string; end: string; allDay?: boolean; color?: string; calendarName?: string; typeName?: string; employeeNames?: string[]; resourceIds?: string[] }
  export const PluginCalendarView: ComponentType<{ title: string; events: PluginCalendarEvent[]; resources?: { id: string; title: string }[]; allowResources?: boolean; showCalendarName?: boolean; canCreate?: boolean; onRangeChange: (range: { start: string; end: string }) => void; onModeChange?: (mode: 'calendar' | 'list' | 'resources') => void; onEventClick?: (id: string) => void; onDateSelect?: (range: { start: string; end: string; allDay: boolean }) => void }>
  export const Sheet: ComponentType<{ open: boolean; onOpenChange: (open: boolean) => void; children?: ReactNode }>
  export const SheetContent: ComponentType<{ children?: ReactNode; finalFocus?: RefObject<HTMLElement | null>; className?: string }>
  export const SheetHeader: ComponentType<{ children?: ReactNode }>
  export const SheetTitle: ComponentType<{ children?: ReactNode }>
  export const SheetDescription: ComponentType<{ children?: ReactNode }>
  export const ConfirmDialog: ComponentType<{ title: string; description: string; pending: boolean; error?: ReactNode; onConfirm: () => void; onCancel: () => void; returnFocus: RefObject<HTMLElement | null>; destructive?: boolean; confirmLabel?: string }>
  export function useTranslation(): { language: string }
  export function registerPluginTranslations(namespace: string, entries: Partial<Record<'fr' | 'en', Record<string, string>>>): void
  export function usePluginTranslation(namespace: string): { language: 'fr' | 'en'; t: (key: string, variables?: Record<string, string | number>) => string }
  export function copyPluginToken(input: HTMLInputElement): Promise<boolean>
  export function apiRequest<T>(url: string, init?: RequestInit): Promise<T>
}

declare module '@labsmanager/plugin-calendar' {
  import type { ComponentType } from 'react'
  export const FullCalendar: ComponentType<Record<string, unknown>>
  export const dayGridPlugin: unknown
  export const timeGridPlugin: unknown
  export const interactionPlugin: unknown
  export const classicThemePlugin: unknown
  export const frLocale: unknown
  export const enGbLocale: unknown
}
