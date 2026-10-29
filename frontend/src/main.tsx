import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, ConfirmDialog, EntityActionMenu, EntitySearch, employeeFilterSources, PluginCalendarView, PluginPublishedCalendarActions, projectFilterSources, teamFilterSources, Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, registerPluginTranslations, usePluginTranslation, type EntitySearchSource, type PluginCalendarEvent } from '@labsmanager/plugin-ui'
import './style.css'
import {
  createCalendar, createEvent, createType, deleteEvent, deleteType, listAttachments, setAttachments as saveAttachments, listCalendars, listEvents, listResources,
  listMembers, listTypes, removeMember, resolveEligibleEmployee, resolveUser, searchEligibleEmployees, searchUsers, setMember, updateCalendar,
  updateEvent, updateType, type Attachment, type Attachments, type Calendar, type CalendarContext, type Event, type EventType, type EventWrite, type Member, type Role,
} from './api'
import fr from './locales/fr.json'
import en from './locales/en.json'

registerPluginTranslations('calendars', { fr, en })

type Range = { start: string; end: string }
type EventDraft = { event: Event | null; range: Range; allDay: boolean }
type Locale = { [K in keyof typeof fr]: string }

function presentationEvent(item: Event, calendarName: string, id: string): PluginCalendarEvent {
  return { id, title: item.title, start: item.start, end: item.end, allDay: item.all_day,
    color: item.color, calendarName, typeName: item.event_type_name,
    employeeNames: item.employees.map((employee) => employee.label),
    resourceIds: item.employees.map((employee) => `employee:${employee.id}`) }
}

function draftRange(start: string, end: string, allDay: boolean): Range {
  if (start !== end) return { start, end }
  return { start, end: allDay ? nextDay(start) : new Date(new Date(start).getTime() + 60 * 60_000).toISOString() }
}

function useLabels(): Locale {
  const { t } = usePluginTranslation('calendars')
  return useMemo(() => Object.fromEntries(Object.keys(fr).map((key) => [key, t(key)])) as Locale, [t])
}

function errorText(error: unknown, fallback: string) {
  if (error && typeof error === 'object' && 'payload' in error) {
    const payload = (error as { payload: unknown }).payload
    if (payload && typeof payload === 'object') {
      const values = Object.values(payload).flatMap((value) => Array.isArray(value) ? value : [value])
      const message = values.find((value): value is string => typeof value === 'string')
      if (message) return message
    }
  }
  return fallback
}

function dateInput(value: string, allDay: boolean) {
  if (allDay) return value.slice(0, 10)
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function eventInput(value: string, allDay: boolean) {
  return allDay ? value : new Date(value).toISOString()
}

function nextDay(value: string) {
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10)
}

function CalendarForm({ calendar, onSaved, onClose, labels }: {
  calendar: Calendar | null; onSaved: (calendar: Calendar) => void; onClose: () => void; labels: Locale
}) {
  const [name, setName] = useState(calendar?.name ?? '')
  const [description, setDescription] = useState(calendar?.description ?? '')
  const [color, setColor] = useState(calendar?.color ?? '#3b82f6')
  const [accessMode, setAccessMode] = useState<Calendar['access_mode']>(calendar?.access_mode ?? 'restricted')
  const [calendarType, setCalendarType] = useState<Calendar['calendar_type']>(calendar?.calendar_type ?? 'events')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setPending(true); setError('')
    try {
      const saved = calendar ? await updateCalendar(calendar.id, { name, description, color, access_mode: accessMode, calendar_type: calendarType }) : await createCalendar({ name, description, color, access_mode: accessMode, calendar_type: calendarType })
      onSaved(saved)
    } catch (reason) { setError(errorText(reason, labels.saveError)) } finally { setPending(false) }
  }
  return <form className="calendars-form" onSubmit={(event) => void submit(event)}>
    <label>{labels.name}<input required maxLength={150} value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>{labels.description}<textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
    <label>{labels.color}<input type="color" value={color} onChange={(event) => setColor(event.target.value)} /></label>
    <label>{labels.calendarType}<select value={calendarType} disabled={Boolean(calendar?.has_events)} onChange={(event) => setCalendarType(event.target.value as Calendar['calendar_type'])}>
      <option value="events">{labels.eventsCalendar}</option><option value="assignments">{labels.assignmentsCalendar}</option>
    </select></label>
    {calendar?.has_events && <p className="calendars-help">{labels.typeLocked}</p>}
    <label>{labels.accessMode}<select value={accessMode} onChange={(event) => setAccessMode(event.target.value as Calendar['access_mode'])}>
      <option value="restricted">{labels.restricted}</option><option value="inherited">{labels.inherited}</option>
    </select></label>
    <p className="calendars-help">{accessMode === 'inherited' ? labels.inheritedHelp : labels.restrictedHelp}</p>
    {error && <Alert tone="danger">{error}</Alert>}
    <div className="calendars-actions"><Button disabled={pending} onClick={onClose}>{labels.cancel}</Button><Button variant="primary" disabled={pending} type="submit">{labels.save}</Button></div>
  </form>
}

function EventForm({ calendar, draft, types, labels, onSaved, onClose }: {
  calendar: Calendar; draft: EventDraft; types: EventType[]; labels: Locale; onSaved: () => void; onClose: () => void
}) {
  const [title, setTitle] = useState(draft.event?.title ?? '')
  const [description, setDescription] = useState(draft.event?.description ?? '')
  const [allDay, setAllDay] = useState(draft.allDay)
  const [start, setStart] = useState(dateInput(draft.range.start, draft.allDay))
  const [end, setEnd] = useState(dateInput(draft.range.end, draft.allDay))
  const [eventType, setEventType] = useState(String(draft.event?.event_type ?? ''))
  const [assigned, setAssigned] = useState<Attachment[]>(draft.event?.employees ?? [])
  const [employeeSelection, setEmployeeSelection] = useState('')
  const employeeSource = useMemo<EntitySearchSource>(() => ({
    async search(query, signal) {
      const response = await searchEligibleEmployees(calendar.id, query, signal)
      return { options: response.results.map((item) => ({ value: String(item.id), label: item.label })), hasMore: response.has_more }
    },
    async resolve(id, signal) {
      const response = await resolveEligibleEmployee(calendar.id, id, signal)
      const item = response.results[0]
      return item ? { value: String(item.id), label: item.label } : null
    },
  }), [calendar.id])
  const [pending, setPending] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState('')
  const focus = useRef<HTMLButtonElement>(null)
  const toggleAllDay = (checked: boolean) => {
    setAllDay(checked)
    const day = start.slice(0, 10)
    setStart(checked ? day : `${day}T09:00`)
    setEnd(checked ? (end.slice(0, 10) > day ? end.slice(0, 10) : nextDay(day)) : `${day}T10:00`)
  }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    let write: EventWrite
    try {
      write = { title, description, start: eventInput(start, allDay), end: eventInput(end, allDay), all_day: allDay, event_type: eventType ? Number(eventType) : null,
        ...(calendar.calendar_type === 'assignments' ? { employees: assigned.map((item) => item.id) } : {}) }
      if (!Number.isFinite(Date.parse(write.start)) || !Number.isFinite(Date.parse(write.end))
          || new Date(write.end) <= new Date(write.start)) { setError(labels.invalidDates); return }
    } catch { setError(labels.invalidDates); return }
    setPending(true); setError('')
    try {
      if (draft.event) await updateEvent(calendar.id, draft.event.id, write)
      else await createEvent(calendar.id, write)
      onSaved()
    } catch (reason) { setError(errorText(reason, labels.saveError)) } finally { setPending(false) }
  }
  const remove = async () => {
    if (!draft.event) return
    setPending(true); setError('')
    try { await deleteEvent(calendar.id, draft.event.id); onSaved() }
    catch (reason) { setError(errorText(reason, labels.saveError)); setConfirmDelete(false) }
    finally { setPending(false) }
  }
  return <>
    <Sheet open onOpenChange={(open) => { if (!open && !pending) onClose() }}><SheetContent>
      <SheetHeader><SheetTitle>{draft.event ? labels.event : labels.newEvent}</SheetTitle><SheetDescription>{calendar.name}</SheetDescription></SheetHeader>
      <form className="calendars-form" onSubmit={(event) => void save(event)}>
        <label>{labels.titleField}<input required maxLength={200} disabled={!calendar.can_edit} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>{labels.description}<textarea disabled={!calendar.can_edit} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <label className="calendars-check"><input type="checkbox" checked={allDay} disabled={!calendar.can_edit} onChange={(event) => toggleAllDay(event.target.checked)} />{labels.allDay}</label>
        <label>{labels.start}<input required type={allDay ? 'date' : 'datetime-local'} disabled={!calendar.can_edit} value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>{labels.end}<input required type={allDay ? 'date' : 'datetime-local'} disabled={!calendar.can_edit} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
        <label>{labels.typeFilter}<select disabled={!calendar.can_edit} value={eventType} onChange={(event) => setEventType(event.target.value)}><option value="">{labels.noType}</option>
          {types.filter((item) => item.is_active || item.id === draft.event?.event_type).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {calendar.calendar_type === 'assignments' && <div className="calendars-assignees">
          <span>{labels.assignedEmployees}</span>
          {calendar.can_edit && <div className="calendars-assignee-picker"><EntitySearch id="calendars-assignee" label={labels.assignedEmployees} value={employeeSelection} source={employeeSource} onChange={setEmployeeSelection} />
            <Button disabled={!employeeSelection || assigned.some((item) => item.id === Number(employeeSelection))} onClick={() => {
              const controller = new AbortController()
              void resolveEligibleEmployee(calendar.id, employeeSelection, controller.signal).then((response) => {
                const item = response.results[0]
                if (item) setAssigned((current) => current.some((existing) => existing.id === item.id) ? current : [...current, item])
                setEmployeeSelection('')
              }, (reason) => setError(errorText(reason, labels.saveError)))
            }}>{labels.add}</Button></div>}
          <ul>{assigned.map((item) => <li key={item.id}><span>{item.label}</span>{calendar.can_edit && <Button onClick={() => setAssigned((current) => current.filter((employee) => employee.id !== item.id))}>{labels.remove}</Button>}</li>)}</ul>
          {assigned.length > 0 && <p className="calendars-help">{labels.assignedHelp}</p>}
        </div>}
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="calendars-actions">
          {draft.event && calendar.can_edit && <Button ref={focus} variant="destructive" disabled={pending} onClick={() => setConfirmDelete(true)}>{labels.delete}</Button>}
          <Button onClick={onClose}>{calendar.can_edit ? labels.cancel : labels.close}</Button>
          {calendar.can_edit && <Button variant="primary" disabled={pending} type="submit">{labels.save}</Button>}
        </div>
      </form>
    </SheetContent></Sheet>
    {confirmDelete && <ConfirmDialog title={labels.deleteEvent} description={labels.deleteEventDescription} pending={pending} error={error && <Alert tone="danger">{error}</Alert>} returnFocus={focus} onCancel={() => setConfirmDelete(false)} onConfirm={() => void remove()} destructive />}
  </>
}

function TypeRow({ calendarId, item, labels, changed }: { calendarId: number; item: EventType; labels: Locale; changed: () => void }) {
  const [name, setName] = useState(item.name)
  const [color, setColor] = useState(item.color || '#3b82f6')
  const [customColor, setCustomColor] = useState(Boolean(item.color))
  const [active, setActive] = useState(item.is_active)
  const [pending, setPending] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState('')
  const focus = useRef<HTMLButtonElement>(null)
  const save = async () => {
    setPending(true); setError('')
    try { await updateType(calendarId, item.id, { name, color: customColor ? color : '', is_active: active }); changed() }
    catch (reason) { setError(errorText(reason, labels.saveError)) } finally { setPending(false) }
  }
  const remove = async () => {
    setPending(true); setError('')
    try { await deleteType(calendarId, item.id); changed() }
    catch (reason) { setError(errorText(reason, labels.saveError)); setConfirmDelete(false) }
    finally { setPending(false) }
  }
  return <div className="calendars-type-row">
    <input aria-label={labels.name} value={name} onChange={(event) => setName(event.target.value)} />
    <label className="calendars-check"><input type="checkbox" checked={customColor} onChange={(event) => setCustomColor(event.target.checked)} />{labels.customColor}</label>
    {customColor && <input aria-label={labels.color} type="color" value={color} onChange={(event) => setColor(event.target.value)} />}
    <label className="calendars-check"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />{labels.active}</label>
    <Button disabled={pending} onClick={() => void save()}>{labels.save}</Button>
    <Button ref={focus} disabled={pending} variant="destructive" onClick={() => setConfirmDelete(true)}>{labels.delete}</Button>
    {error && <Alert tone="danger">{error}</Alert>}
    {confirmDelete && <ConfirmDialog title={labels.deleteType} description={labels.deleteTypeDescription} pending={pending} error={error && <Alert tone="danger">{error}</Alert>} returnFocus={focus} onCancel={() => setConfirmDelete(false)} onConfirm={() => void remove()} destructive />}
  </div>
}

const attachmentSources: Record<keyof Attachments, EntitySearchSource> = {
  employees: employeeFilterSources.allEmployees,
  teams: teamFilterSources.teams,
  projects: projectFilterSources.projects,
}

function AttachmentsEditor({ calendarId, labels, onSaved }: { calendarId: number; labels: Locale; onSaved: (attachments: Attachments) => void }) {
  const [attachments, setAttachments] = useState<Attachments | null>(null)
  const [selection, setSelection] = useState<Record<keyof Attachments, string>>({ employees: '', teams: '', projects: '' })
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [removal, setRemoval] = useState<{ kind: keyof Attachments; item: Attachment } | null>(null)
  const removeFocus = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    listAttachments(calendarId, controller.signal).then(
      (result) => { if (!controller.signal.aborted) setAttachments(result) },
      (reason) => { if (!controller.signal.aborted) setError(errorText(reason, labels.attachmentError)) },
    )
    return () => controller.abort()
  }, [calendarId, labels.attachmentError])
  const change = async (kind: keyof Attachments, ids: number[]) => {
    setPending(true); setError(''); setSuccess(false)
    try {
      const result = await saveAttachments(calendarId, { [kind]: ids })
      setAttachments(result); setSelection((current) => ({ ...current, [kind]: '' })); setRemoval(null); setSuccess(true)
      onSaved(result)
    } catch (reason) { setError(errorText(reason, labels.saveError)); setRemoval(null) }
    finally { setPending(false) }
  }
  if (!attachments) return <div role="status">{error ? <Alert tone="danger">{error}</Alert> : labels.loading}</div>
  return <section className="calendars-attachments">
    <h3>{labels.attachments}</h3>
    {(['employees', 'teams', 'projects'] as const).map((kind) => <div key={kind} className="calendars-attachment-group">
      <label>{labels[kind]}<EntitySearch id={`calendars-${kind}`} label={labels[kind]} value={selection[kind]}
        source={attachmentSources[kind]} onChange={(value) => setSelection((current) => ({ ...current, [kind]: value }))} /></label>
      <Button disabled={pending || !selection[kind] || attachments[kind].some((item) => item.id === Number(selection[kind]))}
        onClick={() => void change(kind, [...attachments[kind].map((item) => item.id), Number(selection[kind])])}>{labels.add}</Button>
      <ul>{attachments[kind].map((item) => <li key={item.id}><span>{item.label}</span>
        <Button ref={removeFocus} disabled={pending} variant="destructive" onClick={() => setRemoval({ kind, item })}>{labels.remove}</Button>
      </li>)}</ul>
    </div>)}
    {success && <Alert tone="success">{labels.saved}</Alert>}
    {error && <Alert tone="danger">{error}</Alert>}
    {removal && <ConfirmDialog title={`${labels.remove} · ${removal.item.label}`} description={labels.removeAttachmentDescription}
      pending={pending} returnFocus={removeFocus} onCancel={() => setRemoval(null)}
      onConfirm={() => void change(removal.kind, attachments[removal.kind].filter((item) => item.id !== removal.item.id).map((item) => item.id))} destructive />}
  </section>
}

type ManageSection = 'settings' | 'types' | 'attachments' | 'members'
function ManageSheet({ calendar, types, typesError, labels, initialSection, onCalendarSaved, onTypesChanged, onRetryTypes, onAttachmentsChanged, onClose }: {
  calendar: Calendar; types: EventType[]; labels: Locale; onCalendarSaved: (calendar: Calendar) => void
  initialSection: ManageSection; typesError: boolean; onTypesChanged: () => void; onRetryTypes: () => void
  onAttachmentsChanged: (attachments: Attachments) => void; onClose: () => void
}) {
  const [section, setSection] = useState<ManageSection>(initialSection)
  const [name, setName] = useState('')
  const [color, setColor] = useState('#3b82f6')
  const [customColor, setCustomColor] = useState(false)
  const [members, setMembers] = useState<Member[]>([])
  const [owner, setOwner] = useState('')
  const [memberId, setMemberId] = useState('')
  const [memberLevel, setMemberLevel] = useState<Role>('view')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const refreshMembers = useCallback(async () => {
    const response = await listMembers(calendar.id)
    setOwner(response.owner.label); setMembers(response.results)
  }, [calendar.id])
  useEffect(() => {
    if (section !== 'members') return
    let live = true
    listMembers(calendar.id).then((response) => { if (live) { setOwner(response.owner.label); setMembers(response.results) } },
      (reason) => { if (live) setError(errorText(reason, labels.noAccess)) })
    return () => { live = false }
  }, [calendar.id, section, labels.noAccess])
  const users = useMemo<EntitySearchSource>(() => ({
    async search(query, signal) {
      const response = await searchUsers(calendar.id, query, signal)
      return { options: response.results.map((user) => ({ value: String(user.id), label: user.label })), hasMore: response.has_more }
    },
    async resolve(id, signal) {
      const response = await resolveUser(calendar.id, id, signal)
      const user = response.results[0]
      return user ? { value: String(user.id), label: user.label } : null
    },
  }), [calendar.id])
  const mutate = async (action: () => Promise<unknown>, after: () => void) => {
    setPending(true); setError('')
    try { await action(); await refreshMembers(); after() }
    catch (reason) { setError(errorText(reason, labels.saveError)) }
    finally { setPending(false) }
  }
  const addType = async (event: React.FormEvent) => {
    event.preventDefault(); setPending(true); setError('')
    try { await createType(calendar.id, { name, color: customColor ? color : '', is_active: true }); setName(''); onTypesChanged() }
    catch (reason) { setError(errorText(reason, labels.saveError)) } finally { setPending(false) }
  }
  return <Sheet open onOpenChange={(open) => { if (!open && !pending) onClose() }}><SheetContent className="calendars-manage-sheet">
    <SheetHeader><SheetTitle>{labels.manage} · {calendar.name}</SheetTitle><SheetDescription>{labels.description}</SheetDescription></SheetHeader>
    <div className="calendars-tabs" role="tablist" aria-label={labels.manage}>
      {(['settings', 'types', 'attachments', 'members'] as const).map((key) => <Button key={key} role="tab" aria-selected={section === key} variant={section === key ? 'primary' : 'ghost'} onClick={() => { setError(''); setSection(key) }}>{labels[key]}</Button>)}
    </div>
    {section === 'settings' && <CalendarForm calendar={calendar} labels={labels} onSaved={onCalendarSaved} onClose={onClose} />}
    {section === 'attachments' && <AttachmentsEditor calendarId={calendar.id} labels={labels} onSaved={onAttachmentsChanged} />}
    {section === 'types' && <div className="calendars-form">
      {typesError && <Alert tone="danger">{labels.typesError} <Button onClick={onRetryTypes}>{labels.retry}</Button></Alert>}
      <form className="calendars-type-row" onSubmit={(event) => void addType(event)}>
        <input required aria-label={labels.newType} placeholder={labels.newType} value={name} onChange={(event) => setName(event.target.value)} />
        <label className="calendars-check"><input type="checkbox" checked={customColor} onChange={(event) => setCustomColor(event.target.checked)} />{labels.customColor}</label>
        {customColor && <input aria-label={labels.color} type="color" value={color} onChange={(event) => setColor(event.target.value)} />}
        <Button type="submit" variant="primary" disabled={pending}>{labels.add}</Button>
      </form>
      {types.map((item) => <TypeRow key={item.id} calendarId={calendar.id} item={item} labels={labels} changed={onTypesChanged} />)}
    </div>}
    {section === 'members' && <div className="calendars-form">
      <p>{labels.owner}: {owner || labels.loading}</p>
      <label>{labels.user}<EntitySearch id="calendars-user" label={labels.user} value={memberId} source={users} onChange={setMemberId} /></label>
      <label>{labels.level}<select value={memberLevel} onChange={(event) => setMemberLevel(event.target.value as Role)}>
        <option value="view">{labels.view}</option><option value="edit">{labels.edit}</option><option value="manage">{labels.manageLevel}</option>
      </select></label>
      <Button variant="primary" disabled={pending || !memberId} onClick={() => void mutate(() => setMember(calendar.id, Number(memberId), memberLevel), () => setMemberId(''))}>{labels.add}</Button>
      <ul className="calendars-members">{members.map((member) => <li key={member.user.id}>
        <span>{member.user.label}</span>
        <select aria-label={`${labels.level} · ${member.user.label}`} value={member.level} disabled={pending} onChange={(event) => void mutate(() => setMember(calendar.id, member.user.id, event.target.value as Role), () => {})}>
          <option value="view">{labels.view}</option><option value="edit">{labels.edit}</option><option value="manage">{labels.manageLevel}</option>
        </select>
        <Button disabled={pending} variant="destructive" onClick={() => void mutate(() => removeMember(calendar.id, member.user.id), () => {})}>{labels.remove}</Button>
      </li>)}</ul>
    </div>}
    {error && <Alert tone="danger">{error}</Alert>}
  </SheetContent></Sheet>
}

export function Page({ context }: { context: Record<string, unknown> }) {
  void context
  const labels = useLabels()
  const [calendars, setCalendars] = useState<Calendar[]>([])
  const [calendarId, setCalendarId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [types, setTypes] = useState<EventType[]>([])
  const [typesError, setTypesError] = useState(false)
  const [typeFilter, setTypeFilter] = useState('')
  const [events, setEvents] = useState<Event[]>([])
  const [range, setRange] = useState<Range | null>(null)
  const [mode, setMode] = useState<'calendar' | 'list' | 'resources'>('calendar')
  const [resourceResult, setResourceResult] = useState<{ key: string; rows: Attachment[] } | null>(null)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState(false)
  const [revision, setRevision] = useState(0)
  const [typesRevision, setTypesRevision] = useState(0)
  const [draft, setDraft] = useState<EventDraft | null>(null)
  const selected = calendars.find((item) => item.id === calendarId) ?? null
  const resourceKey = selected && range ? `${selected.id}:${range.start}:${range.end}` : ''
  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoadError(false)
    try {
      const response = await listCalendars(signal, undefined, 'general')
      if (signal?.aborted) return
      setCalendars(response.results)
      setCalendarId((current) => response.results.some((item) => item.id === current) ? current : response.results[0]?.id ?? null)
    } catch { if (!signal?.aborted) setLoadError(true) }
    finally { if (!signal?.aborted) setLoading(false) }
  }, [])
  useEffect(() => { const controller = new AbortController(); void reload(controller.signal); return () => controller.abort() }, [reload])
  useEffect(() => {
    if (!calendarId) { setTypes([]); return }
    const controller = new AbortController()
    setTypesError(false)
    listTypes(calendarId, controller.signal).then((response) => { if (!controller.signal.aborted) setTypes(response.results) },
      () => { if (!controller.signal.aborted) { setTypes([]); setTypesError(true) } })
    return () => controller.abort()
  }, [calendarId, typesRevision])
  useEffect(() => {
    if (!calendarId || !range) { setEvents([]); return }
    const controller = new AbortController()
    setEvents([]); setEventsLoading(true); setEventsError(false)
    listEvents(calendarId, range.start, range.end, controller.signal).then((response) => {
      if (!controller.signal.aborted) { setEvents(response.results); setEventsLoading(false) }
    }, () => { if (!controller.signal.aborted) { setEventsError(true); setEventsLoading(false) } })
    return () => controller.abort()
  }, [calendarId, range, revision])
  useEffect(() => {
    if (mode !== 'resources' || !selected || selected.calendar_type !== 'assignments' || !range) return
    const controller = new AbortController()
    listResources(selected.id, range.start, range.end, controller.signal).then(
      (result) => { if (!controller.signal.aborted) setResourceResult({ key: resourceKey, rows: result.results }) },
      () => { if (!controller.signal.aborted) setResourceResult({ key: resourceKey, rows: [] }) },
    )
    return () => controller.abort()
  }, [mode, selected?.id, selected?.calendar_type, range, resourceKey])
  const visibleEvents = typeFilter ? events.filter((item) => String(item.event_type ?? '') === typeFilter) : events
  const openCreate = (info: { start: string; end: string; allDay: boolean }) => {
    if (selected?.can_edit) setDraft({ event: null, range: draftRange(info.start, info.end, info.allDay), allDay: info.allDay })
  }
  return <section className="calendars-page">
    {/* <header className="calendars-header"><h1>{labels.title}</h1></header> */}
    {loading && <p role="status">{labels.loading}</p>}
    {loadError && <Alert tone="danger">{labels.loadError} <Button onClick={() => void reload()}>{labels.retry}</Button></Alert>}
    {!loading && !loadError && !calendars.length && <p>{labels.empty}</p>}
    {selected && <>
      <div className="calendars-toolbar">
        <label>{labels.choose}<select value={selected.id} onChange={(event) => { setCalendarId(Number(event.target.value)); setTypeFilter(''); setDraft(null) }}>
          {calendars.map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.name}</option>)}</select></label>
        <label>{labels.typeFilter}<select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
          <option value="">{labels.allTypes}</option>{types.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <PluginPublishedCalendarActions providerSlug="calendars" calendarId={String(selected.id)} />
        {selected.can_edit && <Button onClick={() => { const start = range?.start ?? new Date().toISOString().slice(0, 10); setDraft({ event: null, range: { start, end: nextDay(start) }, allDay: true }) }}>{labels.addEvent}</Button>}
      </div>
      {typesError && <Alert tone="danger">{labels.typesError} <Button onClick={() => setTypesRevision((value) => value + 1)}>{labels.retry}</Button></Alert>}
      {eventsLoading && <p role="status">{labels.loading}</p>}
      {eventsError && <Alert tone="danger">{labels.eventsError} <Button onClick={() => setRevision((value) => value + 1)}>{labels.retry}</Button></Alert>}
      <PluginCalendarView title={selected.name} events={visibleEvents.map((item) => presentationEvent(item, selected.name, String(item.id)))}
        resources={(resourceResult?.key === resourceKey ? resourceResult.rows : []).map((item) => ({ id: `employee:${item.id}`, title: item.label }))}
        allowResources={selected.calendar_type === 'assignments'} canCreate={selected.can_edit} onRangeChange={setRange} onModeChange={setMode}
        onDateSelect={openCreate} onEventClick={(id) => { const item = events.find((event) => event.id === Number(id)); if (item) setDraft({ event: item, range: { start: item.start, end: item.end }, allDay: item.all_day }) }} />
    </>}
    {selected && draft && <EventForm key={`${selected.id}:${draft.event?.id ?? 'new'}:${draft.range.start}`} calendar={selected} draft={draft} types={types} labels={labels} onSaved={() => { setDraft(null); setRevision((value) => value + 1) }} onClose={() => setDraft(null)} />}
  </section>
}

export function SettingsPanel({ context }: { context: Record<string, unknown> }) {
  void context
  const labels = useLabels()
  const [calendars, setCalendars] = useState<Calendar[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<{ calendar: Calendar; section: ManageSection } | null>(null)
  const [types, setTypes] = useState<EventType[]>([])
  const [typesError, setTypesError] = useState(false)
  const typesRequest = useRef(0)
  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoadError(false)
    try {
      const response = await listCalendars(signal, undefined, 'managed')
      if (!signal?.aborted) setCalendars(response.results)
    } catch { if (!signal?.aborted) setLoadError(true) }
    finally { if (!signal?.aborted) setLoading(false) }
  }, [])
  useEffect(() => { const controller = new AbortController(); void reload(controller.signal); return () => controller.abort() }, [reload])
  const refreshTypes = useCallback(async (calendarId: number) => {
    const request = ++typesRequest.current
    setTypesError(false)
    try { const result = await listTypes(calendarId); if (request === typesRequest.current) setTypes(result.results) }
    catch { if (request === typesRequest.current) setTypesError(true) }
  }, [])
  const openManagement = (calendar: Calendar, section: ManageSection) => {
    setTypes([])
    setEditing({ calendar, section })
    void refreshTypes(calendar.id)
  }
  const savedCalendar = () => { setCreateOpen(false); setEditing(null); void reload() }
  const attachmentLabels = (calendar: Calendar) => {
    const groups = calendar.attachments
    if (!groups) return '—'
    const names = (['employees', 'teams', 'projects'] as const).flatMap((key) =>
      groups[key].length ? [`${labels[key]}: ${groups[key].map((item) => item.label).join(', ')}`] : [])
    return names.length ? names.join(' · ') : '—'
  }
  return <div className="calendars-settings">
    <div className="calendars-header"><h3>{labels.managedCalendars}</h3><Button variant="primary" onClick={() => setCreateOpen(true)}>{labels.createCalendar}</Button></div>
    {loading && <p role="status">{labels.loading}</p>}
    {loadError && <Alert tone="danger">{labels.loadError} <Button onClick={() => void reload()}>{labels.retry}</Button></Alert>}
    {!loading && !loadError && !calendars.length && <p>{labels.managedEmpty}</p>}
    {!!calendars.length && <div className="calendars-table-scroll"><table className="calendars-table">
      <thead><tr><th scope="col">{labels.name}</th><th scope="col">{labels.calendarType}</th><th scope="col">{labels.attachments}</th><th scope="col">{labels.actions}</th></tr></thead>
      <tbody>{calendars.map((calendar) => <tr key={calendar.id}>
        <td><span className="calendars-name"><span className="calendars-swatch" style={{ backgroundColor: calendar.color }} aria-hidden="true" />{calendar.name}</span></td>
        <td>{calendar.calendar_type === 'assignments' ? labels.assignmentsCalendar : labels.eventsCalendar}</td>
        <td>{attachmentLabels(calendar)}</td>
        <td>{calendar.can_manage && <EntityActionMenu label={`${labels.actions} · ${calendar.name}`} groups={[[
          { id: 'settings', label: labels.settings, onSelect: () => openManagement(calendar, 'settings') },
          { id: 'types', label: labels.types, onSelect: () => openManagement(calendar, 'types') },
          { id: 'attachments', label: labels.attachments, onSelect: () => openManagement(calendar, 'attachments') },
          { id: 'members', label: labels.members, onSelect: () => openManagement(calendar, 'members') },
        ]]} />}</td>
      </tr>)}</tbody>
    </table></div>}
    {createOpen && <Sheet open onOpenChange={(open) => { if (!open) setCreateOpen(false) }}><SheetContent><SheetHeader><SheetTitle>{labels.createCalendar}</SheetTitle></SheetHeader>
      <CalendarForm calendar={null} labels={labels} onSaved={savedCalendar} onClose={() => setCreateOpen(false)} />
    </SheetContent></Sheet>}
    {editing && <ManageSheet key={`${editing.calendar.id}:${editing.section}`} calendar={editing.calendar} types={types} typesError={typesError} labels={labels} initialSection={editing.section}
      onCalendarSaved={savedCalendar} onTypesChanged={() => void refreshTypes(editing.calendar.id)} onRetryTypes={() => void refreshTypes(editing.calendar.id)}
      onAttachmentsChanged={() => void reload()} onClose={() => { typesRequest.current += 1; setEditing(null) }} />}
  </div>
}

/** One contextual view for Employee, Team and Project tabs. */
export function ContextTab({ context }: { context: Record<string, unknown> }) {
  const labels = useLabels()
  const entity = context.entity
  const objectId = context.objectId
  const valid = (entity === 'employee' || entity === 'team' || entity === 'project') && Number.isInteger(objectId) && Number(objectId) > 0
  const scope = useMemo(() => valid ? { entity, objectId: Number(objectId) } as CalendarContext : null, [entity, objectId, valid])
  const [calendars, setCalendars] = useState<Calendar[]>([])
  const [enabled, setEnabled] = useState<number[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [loadRevision, setLoadRevision] = useState(0)
  const [range, setRange] = useState<Range | null>(null)
  const [mode, setMode] = useState<'calendar' | 'list' | 'resources'>('calendar')
  const [resourceResult, setResourceResult] = useState<{ key: string; rows: Attachment[] } | null>(null)
  const [events, setEvents] = useState<Event[]>([])
  const [eventsError, setEventsError] = useState(false)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const [draft, setDraft] = useState<EventDraft | null>(null)
  const [destinationId, setDestinationId] = useState<number | null>(null)
  const [choosing, setChoosing] = useState<EventDraft | null>(null)
  const [types, setTypes] = useState<EventType[]>([])
  const [typesError, setTypesError] = useState(false)
  const [typesRevision, setTypesRevision] = useState(0)
  const selected = calendars.find((item) => item.id === destinationId) ?? null
  const selectedId = selected?.id
  const draftId = draft?.event?.id
  const draftStart = draft?.range.start
  const editable = calendars.filter((item) => enabled.includes(item.id) && item.can_edit)
  const resourceKey = range ? `${enabled.join(',')}:${range.start}:${range.end}` : ''

  useEffect(() => {
    // Clear stale data before loading a different detail context.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!scope) { setLoading(false); setLoadError(true); return }
    const controller = new AbortController()
    setLoading(true); setLoadError(false); setCalendars([]); setEnabled([]); setEvents([])
    setDraft(null); setChoosing(null); setDestinationId(null)
    listCalendars(controller.signal, scope).then((result) => {
      if (!controller.signal.aborted) {
        setCalendars(result.results)
        setEnabled(result.results.map((item) => item.id))
        setLoading(false)
      }
    }, () => { if (!controller.signal.aborted) { setLoadError(true); setLoading(false) } })
    return () => controller.abort()
  }, [scope, loadRevision])

  useEffect(() => {
    // The previous period must disappear as soon as the visible calendars change.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!range || !enabled.length) { setEvents([]); setEventsLoading(false); return }
    const controller = new AbortController()
    setEvents([]); setEventsLoading(true); setEventsError(false)
    Promise.all(enabled.map((id) => listEvents(id, range.start, range.end, controller.signal))).then(
      (results) => { if (!controller.signal.aborted) { setEvents(results.flatMap((result) => result.results)); setEventsLoading(false) } },
      () => { if (!controller.signal.aborted) { setEventsError(true); setEventsLoading(false) } },
    )
    return () => controller.abort()
  }, [range, enabled, revision])

  useEffect(() => {
    // Keep the type selector tied to the event currently being edited.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!selectedId || !draftStart) { setTypes([]); return }
    const controller = new AbortController()
    setTypesError(false)
    listTypes(selectedId, controller.signal).then(
      (result) => { if (!controller.signal.aborted) setTypes(result.results) },
      () => { if (!controller.signal.aborted) { setTypes([]); setTypesError(true) } },
    )
    return () => controller.abort()
  }, [selectedId, draftId, draftStart, typesRevision])

  useEffect(() => {
    if (mode !== 'resources' || !range) return
    const assignments = calendars.filter((item) => enabled.includes(item.id) && item.calendar_type === 'assignments')
    if (!assignments.length) return
    const controller = new AbortController()
    Promise.all(assignments.map((item) => listResources(item.id, range.start, range.end, controller.signal))).then(
      (results) => { if (!controller.signal.aborted) setResourceResult({ key: resourceKey, rows: [...new Map(results.flatMap((result) => result.results).map((item) => [item.id, item])).values()] }) },
      () => { if (!controller.signal.aborted) setResourceResult({ key: resourceKey, rows: [] }) },
    )
    return () => controller.abort()
  }, [mode, range, enabled, calendars, resourceKey])

  const beginCreate = (next: EventDraft) => {
    if (!editable.length) return
    setDestinationId(null)
    setChoosing(next)
  }
  const openCreate = (info: { start: string; end: string; allDay: boolean }) =>
    beginCreate({ event: null, range: draftRange(info.start, info.end, info.allDay), allDay: info.allDay })
  const openEvent = (id: string) => {
    const item = events.find((event) => `${event.calendar}:${event.id}` === id)
    if (item) { setDestinationId(item.calendar); setDraft({ event: item, range: { start: item.start, end: item.end }, allDay: item.all_day }) }
  }
  return <section className="calendars-page calendars-context">
    {loading && <p role="status">{labels.loading}</p>}
    {loadError && <Alert tone="danger">{labels.loadError} <Button onClick={() => setLoadRevision((value) => value + 1)}>{labels.retry}</Button></Alert>}
    {!loading && !loadError && !calendars.length && <p>{labels.contextualEmpty}</p>}
    {!!calendars.length && <>
      {!!editable.length && <div className="calendars-toolbar"><Button onClick={() => { const start = range?.start ?? new Date().toISOString().slice(0, 10); beginCreate({ event: null, range: { start, end: nextDay(start) }, allDay: true }) }}>{labels.addEvent}</Button></div>}
      <fieldset className="calendars-context-choices"><legend>{labels.visibleCalendars}</legend>
        {calendars.map((calendar) => <div key={calendar.id} className="calendars-context-item">
          <label className="calendars-context-choice"><input type="checkbox" checked={enabled.includes(calendar.id)} onChange={(event) => setEnabled((current) => event.target.checked ? [...current, calendar.id] : current.filter((id) => id !== calendar.id))} />
            <span className="calendars-context-swatch" style={{ backgroundColor: calendar.color }} />{calendar.name}</label>
          <PluginPublishedCalendarActions providerSlug="calendars" calendarId={String(calendar.id)} />
        </div>)}
      </fieldset>
      {eventsLoading && <p role="status">{labels.loading}</p>}
      {eventsError && <Alert tone="danger">{labels.eventsError} <Button onClick={() => setRevision((value) => value + 1)}>{labels.retry}</Button></Alert>}
      <PluginCalendarView title={labels.title} events={events.filter((item) => enabled.includes(item.calendar)).map((item) => {
        const name = calendars.find((calendar) => calendar.id === item.calendar)?.name ?? ''
        return presentationEvent(item, name, `${item.calendar}:${item.id}`)
      })} showCalendarName allowResources={calendars.some((item) => enabled.includes(item.id) && item.calendar_type === 'assignments')}
        resources={(resourceResult?.key === resourceKey ? resourceResult.rows : []).map((item) => ({ id: `employee:${item.id}`, title: item.label }))}
        canCreate={editable.length > 0} onRangeChange={setRange} onModeChange={setMode} onDateSelect={openCreate} onEventClick={openEvent} />
    </>}
    {choosing && <Sheet open onOpenChange={(open) => { if (!open) setChoosing(null) }}><SheetContent>
      <SheetHeader><SheetTitle>{labels.chooseDestination}</SheetTitle></SheetHeader>
      <div className="calendars-form"><label>{labels.choose}<select value={destinationId ?? ''} onChange={(event) => setDestinationId(Number(event.target.value) || null)}>
        <option value="">—</option>{editable.map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.name}</option>)}
      </select></label><div className="calendars-actions"><Button onClick={() => setChoosing(null)}>{labels.cancel}</Button>
      <Button variant="primary" disabled={!destinationId} onClick={() => { setDraft(choosing); setChoosing(null) }}>{labels.continue}</Button></div></div>
    </SheetContent></Sheet>}
    {typesError && <Alert tone="danger">{labels.typesError} <Button onClick={() => setTypesRevision((value) => value + 1)}>{labels.retry}</Button></Alert>}
    {selected && draft && <EventForm key={`${selected.id}:${draft.event?.id ?? 'new'}:${draft.range.start}`} calendar={selected} draft={draft} types={types} labels={labels}
      onSaved={() => { setDraft(null); setRevision((value) => value + 1) }} onClose={() => setDraft(null)} />}
  </section>
}
