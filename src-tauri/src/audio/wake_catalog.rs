//! The model picker and Rust inference share the same pinned asset manifest.
use std::path::Path;

use serde::Deserialize;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
pub struct WakeModel {
    pub id: String,
    pub filename: String,
    pub kind: String,
    pub sha256: String,
    pub embedding_frames: usize,
    pub score_start: usize,
}

pub fn catalog() -> Vec<WakeModel> {
    serde_json::from_str(include_str!("../../../voice/wakeword_catalog.json"))
        .expect("bundled wake-word catalog")
}

pub fn wake_model(id: &str) -> Result<WakeModel, String> {
    catalog()
        .into_iter()
        .find(|m| m.id == id && m.kind == "wake")
        .ok_or_else(|| "Choose a wake word from Settings → Voice → Activation.".into())
}

pub fn verify_models(dir: &Path, id: &str) -> Result<WakeModel, String> {
    let selected = wake_model(id)?;
    for m in catalog()
        .into_iter()
        .filter(|m| m.kind == "shared" || m.id == id)
    {
        let bytes = std::fs::read(dir.join(&m.filename))
            .map_err(|_| "Download the selected wake word and shared models in Settings → Voice → Activation.".to_string())?;
        if format!("{:x}", Sha256::digest(bytes)) != m.sha256 {
            return Err(
                "A wake-word model needs a fresh download in Settings → Voice → Activation.".into(),
            );
        }
    }
    Ok(selected)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stock_filenames_and_shapes_are_shared_with_picker() {
        let jarvis = wake_model("hey_jarvis").unwrap();
        assert_eq!(jarvis.filename, "hey_jarvis_v0.1.onnx");
        assert_eq!(jarvis.embedding_frames, 16);
        assert_eq!(wake_model("timer").unwrap().embedding_frames, 34);
        assert_eq!(wake_model("weather").unwrap().embedding_frames, 22);
        assert!(wake_model("../hey_orbis").is_err());
        assert!(wake_model("melspectrogram").is_err());
    }

    #[test]
    fn missing_and_corrupt_models_cannot_arm() {
        let dir = std::env::temp_dir().join(format!("orbis-wake-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(verify_models(&dir, "hey_orbis").is_err());
        for m in catalog() {
            std::fs::write(dir.join(m.filename), b"corrupt").unwrap();
        }
        assert!(verify_models(&dir, "hey_orbis").is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
