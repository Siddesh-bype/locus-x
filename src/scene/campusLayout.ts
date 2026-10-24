// Generated from OpenStreetMap (c) OpenStreetMap contributors, ODbL.
// Enriched with real-world architectural metadata for VIT Pune Bibwewadi campus.
export type Polygon = [number, number][];

export interface CampusBlock {
  name: string;
  buildingNumber: string;
  polygon: Polygon;
  height: number;
  stories: number;
  style: 'academic' | 'canteen' | 'sports';
  description?: string;
  hideFor?: string;
  onlyFor?: string;
}

/** Campus-metre point that is each detailed environment's (0, 0). */
export const CAMPUS_ORIGIN: Record<string, [number, number]> = {
  vit_pune_v3: [-3.0, -13.0],
  vit_pune_west_v3: [-77.0, -36.7],
  vit_pune_east_v3: [54.3, 47.1],
  vit_pune_north_v3: [-94.4, 5.2],
};

export const CAMPUS_BLOCKS: CampusBlock[] = [
  {
    name: "Building 1 (Main Academic Block)",
    buildingNumber: "Bldg 1",
    polygon: [[-3.2, 23.1], [-3.4, 12.3], [20.3, 11.4], [20.3, 7.1], [15.5, 7.2], [15.4, 0.9], [15.1, -23.2], [14.7, -53.1], [29.5, -53.7], [30.9, 6.8], [27.1, 6.9], [27.2, 11.2], [56.8, 11.1], [56.9, 22.5]],
    height: 14.0,
    stories: 4,
    style: "academic",
    description: "Administration, Director's Office, Classrooms & Seminar Hall",
    hideFor: "vit_pune_v3",
  },
  {
    name: "Building 1 Annex",
    buildingNumber: "Bldg 1 Annex",
    polygon: [[15.2, -13.2], [15.1, -23.2], [14.7, -53.1], [29.5, -53.7], [30.4, -13.2]],
    height: 14.0,
    stories: 4,
    style: "academic",
    description: "Southern academic wing",
    onlyFor: "vit_pune_v3",
  },
  {
    name: "Building 2 (West Block · Mechanical & Civil)",
    buildingNumber: "Bldg 2",
    polygon: [[-77.0, -25.0], [-76.5, -36.7], [-43.4, -36.7], [-45.4, 14.0], [-55.8, 14.0], [-54.7, -20.0], [-58.7, -24.5]],
    height: 10.5,
    stories: 3,
    style: "academic",
    description: "Mechanical, Civil workshops & Fluid power labs",
    hideFor: "vit_pune_west_v3",
  },
  {
    name: "Building 4 (North Block · Computer & Electronics)",
    buildingNumber: "Bldg 4",
    polygon: [[-94.4, 5.4], [-93.8, 40.6], [-48.8, 80.8], [43.4, 94.5], [47.1, 70.0], [42.9, 69.4], [44.4, 59.7], [19.1, 55.9], [18.0, 63.5], [12.7, 62.7], [10.6, 76.9], [-35.9, 69.9], [-35.2, 64.7], [-74.3, 32.1], [-74.4, 27.1], [-82.0, 27.3], [-82.4, 5.2]],
    height: 14.0,
    stories: 4,
    style: "academic",
    description: "Computer Eng, AI & Data Science, IoT & Embedded systems",
    hideFor: "vit_pune_north_v3",
  },
  {
    name: "Building 3 (East Block · Central Library & IT)",
    buildingNumber: "Bldg 3",
    polygon: [[54.3, 88.8], [56.7, 72.8], [59.1, 73.0], [60.1, 67.7], [57.6, 67.4], [61.0, 47.1], [101.3, 52.5], [99.9, 62.0], [69.9, 57.9], [67.8, 69.1], [69.7, 69.4], [68.6, 74.7], [66.7, 74.3], [65.6, 80.4], [95.3, 84.8], [93.7, 94.8]],
    height: 14.0,
    stories: 4,
    style: "academic",
    description: "Bajrangdas Lohiya Central Library, Reading Hall & IT Labs",
    hideFor: "vit_pune_east_v3",
  },
  {
    name: "Campus Central Canteen",
    buildingNumber: "Canteen",
    polygon: [[-33.4, -70.4], [-31.4, -83.5], [-4.5, -79.5], [-6.3, -66.7]],
    height: 4.5,
    stories: 1,
    style: "canteen",
    description: "Central student dining hall and cafeteria",
  },
  {
    name: "VIT Fruit Canteen",
    buildingNumber: "Fruit Canteen",
    polygon: [[27.7, 49.2], [40.5, 50.6], [41.7, 39.1], [29.0, 37.7]],
    height: 3.8,
    stories: 1,
    style: "canteen",
    description: "Outdoor juice and snack pavilion",
  },
];

export const CAMPUS_GROUND: { name: string; polygon: Polygon; color: string }[] = [
  {
    name: "Campus",
    polygon: [[-100.8, -43.7], [-43.3, -38.9], [-36.3, -93.2], [36.8, -80.5], [35.2, -32.0], [49.7, -31.2], [49.7, -18.9], [63.6, -18.9], [98.9, 34.4], [109.0, 65.8], [109.0, 93.1], [101.3, 102.7], [-49.2, 84.6], [-101.1, 48.3]],
    color: "#e8ede4",
  },
  {
    name: "VIT Sports Ground",
    polygon: [[-33.6, 16.9], [-33.7, -30.2], [-32.9, -41.5], [-23.8, -49.7], [-11.4, -53.1], [-8.1, -52.6], [1.7, -51.7], [1.7, -6.7], [-7.8, -3.5], [-12.9, -0.0], [-16.3, 4.4], [-16.7, 16.8]],
    color: "#5b8444",
  },
];

/** Campus architectural trees: [x, y, radius, height] */
export const CAMPUS_TREES: [number, number, number, number][] = [
  // West boundary & parking verge
  [-84.0, -12.0, 2.2, 5.5],
  [-87.0, 2.0, 2.4, 6.0],
  [-90.0, 22.0, 2.5, 6.2],
  [-85.0, 36.0, 2.0, 5.2],
  // Playground perimeter trees (along running track borders)
  [-36.0, 12.0, 2.6, 6.5],
  [-36.0, -4.0, 2.4, 5.8],
  [-35.0, -18.0, 2.5, 6.2],
  [-35.0, -32.0, 2.7, 6.8],
  [-27.0, -48.0, 2.3, 5.6],
  [-16.0, -52.0, 2.6, 6.4],
  [-1.0, -51.0, 2.2, 5.5],
  [8.0, -50.0, 2.4, 6.0],
  // Walkway avenue between Main block and Playground
  [-5.0, -4.0, 2.0, 5.0],
  [-8.0, 8.0, 2.3, 5.8],
  [-2.0, 17.0, 2.1, 5.2],
  // Courtyard between Main block and Canteen
  [4.0, -32.0, 2.5, 6.2],
  [9.0, -40.0, 2.2, 5.5],
  [3.0, -46.0, 2.0, 5.0],
  // Courtyard between Main block and East/Fruit canteen
  [21.0, 36.0, 2.4, 6.0],
  [24.0, 43.0, 2.1, 5.3],
  [34.0, 33.0, 2.5, 6.3],
  [44.0, 38.0, 2.2, 5.4],
  // East block garden perimeter
  [49.0, 62.0, 2.6, 6.5],
  [51.0, 76.0, 2.4, 6.0],
  [61.0, 84.0, 2.7, 6.8],
  [74.0, 70.0, 2.3, 5.7],
  // North boundary trees
  [-68.0, 52.0, 2.6, 6.4],
  [-48.0, 72.0, 2.5, 6.2],
  [-18.0, 76.0, 2.7, 6.7],
  [6.0, 79.0, 2.4, 6.0],
  [28.0, 86.0, 2.5, 6.3],
];
