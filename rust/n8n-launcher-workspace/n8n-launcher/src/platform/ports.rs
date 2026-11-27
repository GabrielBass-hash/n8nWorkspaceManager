//! Port availability.
//! Port de `src/n8n_launcher/platform/ports.py`.

use std::net::TcpListener;

/// Return True when ``host:port`` can be bound (i.e. nothing is listening).
///
/// The probe binds the wildcard address rather than the loopback one because
/// BSD sockets (macOS) let a specific address bind succeed *next to* an
/// existing wildcard listener when ``SO_REUSEADDR`` is set.
pub fn is_port_available(port: u16, host: Option<&str>) -> bool {
    let host = host.unwrap_or("0.0.0.0");
    match (host, port) {
        (h, p) if h == "0.0.0.0" => TcpListener::bind(("0.0.0.0", p)).is_ok(),
        (h, p) => TcpListener::bind((h, p)).is_ok(),
    }
}

/// Return the first free port at/after ``requested``, skipping ``reserved``.
pub fn suggest_port(requested: u16, reserved: Option<&[u16]>, limit: u16) -> Result<u16, String> {
    let reserved_ports: std::collections::HashSet<u16> = reserved
        .map(|r| r.iter().copied().collect())
        .unwrap_or_default();
    for port in requested..requested.saturating_add(limit) {
        if !reserved_ports.contains(&port) && is_port_available(port, None) {
            return Ok(port);
        }
    }
    Err(format!(
        "No available port found in range {requested}-{}",
        requested.saturating_add(limit.saturating_sub(1))
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_port_available_known_free() {
        // A very high port is likely free
        assert!(is_port_available(59999, None));
    }

    #[test]
    fn test_suggest_port_returns_requested_when_free() {
        let port = suggest_port(59998, None, 10).unwrap();
        assert_eq!(port, 59998);
    }

    #[test]
    fn test_suggest_port_skips_reserved() {
        let port = suggest_port(59998, Some(&[59998]), 10).unwrap();
        assert_ne!(port, 59998);
        assert!(port >= 59999);
    }

    #[test]
    fn test_suggest_port_no_available() {
        // Reserve a whole range to force failure
        let reserved: Vec<u16> = (59900..=59999).collect();
        let result = suggest_port(59900, Some(&reserved), 100);
        assert!(result.is_err());
    }
}
