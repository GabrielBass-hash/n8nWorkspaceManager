//! Interface iced du lanceur : thème, icônes, composants et pages.
//!
//! La logique métier reste dans `app` et `workspaces` ; ce module ne contient
//! que de la présentation, afin qu'aucune règle d'action ne soit réimplémentée
//! dans la vue.

pub mod icons;
pub mod pages;
pub mod theme;
pub mod widgets;
