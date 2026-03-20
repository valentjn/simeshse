/* Copyright (C) 2026 Julian Valentin
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 */

const TARGET_WIDTH = 4096;
const TARGET_HEIGHT = 4096;

/**
 * Asynchronously upload media items for a specified section.
 *
 * @param {string} rootPath Root path for API endpoints, injected from template.
 * @param {string|number} sectionId Section identifier used to locate file input and fieldset elements.
 * @returns {Promise<void>} Promise resolving when upload completes or fails.
 */
const uploadMediaItems = async (rootPath, sectionId) => {
    const fileInput = document.getElementById(`to_be_uploaded_file_input_${sectionId}`);
    if (!fileInput) {
        alert("file input element not found");
        return;
    }
    const fieldset = document.getElementById(`upload_fieldset_${sectionId}`);
    if (fieldset) fieldset.disabled = true;

    const formData = new FormData();
    const createdAts = [];

    for (const file of fileInput.files) {
        let mediaItem;
        try {
            if (file.type.startsWith("image/")) {
                mediaItem = await preprocessImage(file);
            } else if (file.type.startsWith("video/")) {
                mediaItem = await preprocessVideo(file);
            } else {
                alert(`could not preprocess ${file.name}: unknown file type ${file.type}`);
                continue;
            }
        } catch (exception) {
            alert(`could not preprocess ${file.name}: ${exception}`);
            continue;
        }

        const processedFile = new File([mediaItem.binary], file.name, { type: file.type });
        formData.append("files", processedFile);
        createdAts.push(mediaItem.created_at);
    }

    formData.append("created_ats", JSON.stringify(createdAts));

    try {
        const response = await fetch(`${rootPath}/admin/sections/${sectionId}/media-items`, {
            method: "PUT",
            body: formData,
        });
        if (!response.ok) {
            throw new Error(`${response.statusText}: ${await response.text()}`);
        }
        // work around Firefox dropping fragments after being redirected
        body = await response.json();
        window.location = body.redirect_url;
        window.location.reload();
    } catch (error) {
        alert(`upload failed: ${error}`);
    }
};

/**
 * Load, rotate, resize, and extract metadata from an image file.
 *
 * @param {File} file Image file to preprocess.
 * @returns {Promise<{binary: Uint8Array, created_at: number}>} Promise resolving to processed image binary and creation
 * timestamp.
 */
const preprocessImage = async (file) => {
    const image = await loadImage(file);
    const exifData = await readExifData(file);
    const canvas = document.createElement("canvas");
    drawImageOnCanvas(canvas, image, TARGET_WIDTH, TARGET_HEIGHT, getRotationAngleFromExifData(exifData));
    return {
        binary: await getBinaryImageFromCanvas(canvas, file.type),
        created_at: getCreatedAtFromExifData(exifData),
    };
};

/**
 * Load an image file and return an HTMLImageElement.
 *
 * @param {File} file Image file to load.
 * @returns {Promise<HTMLImageElement>} Promise resolving to loaded image element.
 */
const loadImage = (file) =>
    new Promise((resolve, reject) => {
        const image = new window.Image();
        image.onload = () => resolve(image);
        image.onerror = reject;
        image.src = URL.createObjectURL(file);
    });

/**
 * Extract EXIF metadata from an image file using the inkjet library.
 *
 * @param {File} file Image file to extract EXIF data from.
 * @returns {Promise<Object>} Promise resolving to EXIF metadata object.
 */
const readExifData = async (file) => {
    const fileBinary = await readFile(file);
    return new Promise((resolve, reject) => {
        if (typeof inkjet === "undefined" || !inkjet?.exif) {
            reject("inkjet library not loaded");
            return;
        }
        inkjet.exif(fileBinary, (error, exifData) => {
            if (error) {
                reject(error);
            } else {
                resolve(exifData);
            }
        });
    });
};

/**
 * Determine rotation angle in radians based on EXIF orientation value.
 *
 * @param {Object} exifData EXIF metadata object containing orientation.
 * @returns {number} Rotation angle in radians.
 */
const getRotationAngleFromExifData = (exifData) => {
    if (!exifData) return 0.0;
    const exifOrientation = exifData.Orientation;
    switch (exifOrientation) {
        case 6:
            return Math.PI / 2.0;
        case 3:
            return Math.PI;
        case 8:
            return (3.0 * Math.PI) / 2.0;
        default:
            return 0.0;
    }
};

/**
 * Extract creation timestamp from EXIF data, or return current time if unavailable.
 *
 * @param {Object} exifData EXIF metadata object possibly containing DateTimeOriginal.
 * @returns {number} Unix timestamp (seconds) for creation time.
 */
const getCreatedAtFromExifData = (exifData) => {
    const now = Math.floor(Date.now() / 1000);
    const dateTime = exifData?.DateTimeOriginal?.value?.[0];
    if (!dateTime) return now;
    const match = dateTime.match(/(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
    if (!match) return now;
    return Math.floor(
        Date.UTC(
            parseInt(match[1]),
            parseInt(match[2]) - 1,
            parseInt(match[3]),
            parseInt(match[4]),
            parseInt(match[5]),
            parseInt(match[6]),
        ) / 1000,
    );
};

/**
 * Draw an image onto a canvas, resizing and rotating as needed.
 *
 * @param {HTMLCanvasElement} canvas Canvas element to draw on.
 * @param {HTMLImageElement} image Image element to draw.
 * @param {number} targetWidth Maximum width for resizing.
 * @param {number} targetHeight Maximum height for resizing.
 * @param {number} [angle=0.0] Rotation angle in radians (optional).
 */
const drawImageOnCanvas = (canvas, image, targetWidth, targetHeight, angle = 0.0) => {
    const oldWidth = image.width;
    const oldHeight = image.height;
    let newWidth = oldWidth;
    let newHeight = oldHeight;
    if (oldWidth > targetWidth || oldHeight > targetHeight) {
        const aspectRatio = oldWidth / oldHeight;
        if (aspectRatio * targetHeight >= targetWidth) {
            newWidth = Math.round(aspectRatio * targetHeight);
            newHeight = targetHeight;
        } else {
            newWidth = targetWidth;
            newHeight = Math.round(targetWidth / aspectRatio);
        }
    }
    canvas.width = newWidth;
    canvas.height = newHeight;
    const ctx = canvas.getContext("2d");
    ctx.save();
    ctx.translate(newWidth / 2, newHeight / 2);
    if (angle !== 0.0) {
        ctx.rotate(angle);
    }
    ctx.drawImage(image, -newWidth / 2, -newHeight / 2, newWidth, newHeight);
    ctx.restore();
};

/**
 * Convert a canvas to a binary image (Uint8Array) in the specified format.
 *
 * @param {HTMLCanvasElement} canvas Canvas element containing image data.
 * @param {string} type MIME type for output image (e.g., 'image/jpeg').
 * @returns {Promise<Uint8Array>} Promise resolving to binary image data.
 */
const getBinaryImageFromCanvas = async (canvas, type) => {
    const quality = 0.95;
    return new Promise((resolve) => {
        canvas.toBlob(
            async (blob) => {
                const arrayBuffer = await blob.arrayBuffer();
                resolve(new Uint8Array(arrayBuffer));
            },
            type,
            quality,
        );
    });
};

/**
 * Read a video file as binary data for upload.
 *
 * @param {File} file Video file to preprocess.
 * @returns {Promise<{binary: Uint8Array, created_at: number}>} Promise resolving to video binary and dummy creation
 * timestamp.
 */
const preprocessVideo = async (file) => ({
    binary: await readFile(file),
    created_at: 0,
});

/**
 * Read a file as Uint8Array asynchronously.
 *
 * @param {File} file File object to read as binary.
 * @returns {Promise<Uint8Array>} Promise resolving to file contents as Uint8Array.
 */
const readFile = (file) =>
    new Promise((resolve, reject) => {
        const fileReader = new FileReader();
        fileReader.onload = () => resolve(new Uint8Array(fileReader.result));
        fileReader.onerror = reject;
        fileReader.readAsArrayBuffer(file);
    });
