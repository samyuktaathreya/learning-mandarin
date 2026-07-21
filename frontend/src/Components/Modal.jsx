import { useEffect } from 'react';

/**
 * Minimal modal: dimmed overlay + centered box. No dependencies.
 *
 * Props:
 *   - open: boolean
 *   - onClose: () => void            (called on overlay click + Escape)
 *   - title: string
 *   - children: body content
 *   - actions: node (buttons)        rendered in the footer row
 *
 * Closing is deliberate-only for the caller's buttons; overlay/Escape call
 * onClose so a warning modal can treat that as "cancel" (the safe default).
 */
export default function Modal({ open, onClose, title, children, actions }) {
    useEffect(() => {
        if (!open) return;
        const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    if (!open) return null;

    return (
        <div
            className="modal-overlay"
            onClick={onClose}
            style={{
                position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                zIndex: 1000,
            }}
        >
            <div
                className="modal-box"
                role="dialog"
                aria-modal="true"
                onClick={(e) => e.stopPropagation()}
                style={{
                    background: '#fff', color: '#111', maxWidth: 440, width: '90%',
                    padding: '1.5rem', borderRadius: 8, boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
                }}
            >
                {title && <h3 style={{ margin: '0 0 0.75rem' }}>{title}</h3>}
                <div className="modal-body" style={{ marginBottom: '1.25rem', lineHeight: 1.5 }}>
                    {children}
                </div>
                <div
                    className="modal-actions"
                    style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}
                >
                    {actions}
                </div>
            </div>
        </div>
    );
}