#pragma once

// Physical table surface and the projector image mapped onto it.
// Everything in the app that needs a table position works in mm and
// converts here, so a projector or table change is a one-place edit.

// Plywood top: 60 x 36 inches.
static const float TABLE_W_MM = 1524.0f;
static const float TABLE_H_MM = 914.4f;

// Projector native resolution, image assumed to cover the whole table.
static const int PROJ_W_PX = 1920;
static const int PROJ_H_PX = 1080;

// mm along the table -> projector pixel. Axis-independent scales because
// the table's aspect (1.667) is not the projector's (1.778).
inline float mmToPxX(float mm){
	return mm * (float)PROJ_W_PX / TABLE_W_MM;
}

inline float mmToPxY(float mm){
	return mm * (float)PROJ_H_PX / TABLE_H_MM;
}
