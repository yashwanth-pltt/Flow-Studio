'use client';
import { Search, X } from 'lucide-react';
import { useI18n } from '@/lib/i18n';

type Props = { label: string; placeholder: string; value: string; onChange: (value: string) => void };
export function CollectionSearch({ label, placeholder, value, onChange }: Props) {
  const { t } = useI18n();
  return <div className="collection-search">
    <Search size={15} aria-hidden="true" />
    <input type="search" aria-label={label} placeholder={placeholder} value={value} onChange={event => onChange(event.target.value)} />
    {value && <button type="button" aria-label={t('Clear {label}', { label })} onClick={() => onChange('')}><X size={14} /></button>}
  </div>;
}
